"""Единственное место, где есть браузер.

Токен снимается **перехватом сетевой активности страницы**, а не выковыриванием
из внутреннего состояния приложения. Это принципиально: внутреннее устройство
фронтенда 2ГИС меняется когда угодно, а адрес сокета с токеном в параметре —
это их протокол, он живёт дольше.

Ловим тремя сетями, от надёжной к запасной:

1. ``page.on("websocket")`` — событие Playwright. Тот самый ``user/ws``.
2. ``page.on("request")``   — адреса и заголовки обычных запросов.
3. Подмена ``WebSocket`` и ``fetch`` скриптом на странице — на случай, если
   что-то пройдёт мимо первых двух.

Первые два способа работают на уровне протокола и не зависят от того, что
страница делает со своими объектами; третий остался от предыдущего подхода и
стоит дёшево, поэтому пусть будет.

Авторизацию программа не реализует и реализовывать не собирается: вход по
телефону и SMS защищён капчей и проверками устройства, а повторять это в коде —
долгая и хрупкая работа. Логинится настоящий браузер, руками пользователя;
код только забирает результат.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    BrowserMissing,
    PlaywrightMissing,
    SessionExpired,
    SessionMissing,
    TokenNotFound,
)
from .state import harden, prepare_parent
from .tokens import Found, TokenCollector

log = logging.getLogger(__name__)

#: Страница, на которой открывается сокет с токеном.
DEFAULT_URL = "https://2gis.ru/"

DEFAULT_TIMEOUT = 60.0
LOGIN_TIMEOUT = 600.0

#: Сколько ждать источник понадёжнее после первой находки.
#:
#: Перехватчик на странице срабатывает на доли секунды раньше события
#: websocket, и без этой отсрочки возвращался бы он — а он же может ухватить
#: посторонний sha1. Пауза короткая: на живой странице события приходят
#: подряд.
GRACE = 2.0

#: Запасной перехватчик на самой странице.
#:
#: Шаблон здесь — сорок шестнадцатеричных знаков. В соседнем проекте на этом
#: месте стоял шаблон JWT с точками, и он не находил ничего: наш токен точек
#: не содержит. Ошибка стоила времени, повторять её не надо.
HOOK_JS = r"""
() => {
  window.__2gisToken = window.__2gisToken || null;
  const grab = (value) => {
    if (!value || window.__2gisToken) return;
    const match = String(value).match(/\b[0-9a-f]{40}\b/);
    if (match) window.__2gisToken = match[0];
  };
  const NativeWebSocket = window.WebSocket;
  window.WebSocket = function (url, protocols) {
    grab(url);
    const socket = new NativeWebSocket(url, protocols);
    return socket;
  };
  window.WebSocket.prototype = NativeWebSocket.prototype;
  const nativeFetch = window.fetch;
  window.fetch = (...args) => {
    grab(String(args[0]));
    const options = args[1];
    if (options && options.headers) grab(JSON.stringify(options.headers));
    return nativeFetch(...args);
  };
}
"""


@dataclass(frozen=True)
class Capture:
    """Пойманный токен и то, откуда он взялся."""

    token: str
    source: str


def _import_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:  # pragma: no cover — зависит от окружения
        raise PlaywrightMissing(
            "не установлен Playwright.\n"
            "  pip install playwright\n"
            "  2gis-token install-browser"
        ) from error
    return async_playwright


def _launch_failure(error: Exception) -> Exception:
    """Отличает «браузер не скачан» от прочих бед запуска."""
    text = str(error)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return BrowserMissing(
            "Playwright есть, а браузера у него нет. Скачать:\n"
            "  2gis-token install-browser"
        )
    return error


def _attach(page, collector: TokenCollector) -> None:
    """Подписывается на сетевые события страницы."""
    page.on("websocket", lambda ws: collector.offer_websocket_url(ws.url))

    def on_request(request) -> None:
        collector.offer_request_url(request.url)
        try:
            collector.offer_headers(request.headers)
        except Exception:  # заголовки могут быть уже недоступны — не повод падать
            pass

    page.on("request", on_request)


def enough(found: Found | None, waited: float, grace: float = GRACE) -> bool:
    """Хватит ли найденного или стоит подождать источник понадёжнее.

    Адрес сокета сомнений не вызывает — с ним останавливаемся сразу. Всё
    остальное держим ``grace`` секунд: за это время обычно приезжает тот самый
    ``user/ws``, и он вытеснит случайную находку.
    """
    if found is None:
        return False
    if found.source == "websocket":
        return True
    return waited >= grace


async def _wait_for_token(page, collector: TokenCollector, timeout: float) -> Capture | None:
    """Ждёт токен до истечения срока, попутно заглядывая в перехватчик на странице."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    first_found_at: float | None = None

    while loop.time() < deadline:
        try:
            collector.offer_text(await page.evaluate("window.__2gisToken"))
        except Exception:
            # страницу могли перезагрузить прямо сейчас — попробуем в следующий раз
            pass

        if collector.found and first_found_at is None:
            first_found_at = loop.time()
        if enough(collector.found, loop.time() - (first_found_at or loop.time())):
            break

        await asyncio.sleep(0.25)

    found = collector.found
    return Capture(found.token, found.source) if found else None


async def capture_token(
    state_path: Path,
    *,
    url: str = DEFAULT_URL,
    timeout: float = DEFAULT_TIMEOUT,
    headless: bool = True,
) -> Capture:
    """Заходит на 2ГИС с сохранённой сессией и забирает токен."""
    if not state_path.exists():
        raise SessionMissing(
            f"нет сохранённой сессии ({state_path}).\n"
            "Войди один раз:  2gis-token login"
        )

    async_playwright = _import_playwright()
    collector = TokenCollector()

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless)
        except Exception as error:  # pragma: no cover — зависит от окружения
            raise _launch_failure(error) from error
        context = await browser.new_context(storage_state=str(state_path))
        try:
            page = await context.new_page()
            _attach(page, collector)
            await page.add_init_script(f"({HOOK_JS})()")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            capture = await _wait_for_token(page, collector, timeout)
            if capture is None:
                raise SessionExpired(
                    f"токен не появился за {timeout:.0f} с.\n"
                    "Чаще всего это значит, что сессия истекла — войди заново:\n"
                    "  2gis-token login"
                )
            # Куки могли обновиться за время визита — сохраняем, чтобы сессия
            # жила дольше и следующий запуск не упёрся в протухшие.
            await context.storage_state(path=str(state_path))
            harden(state_path)
            log.info("токен получен из источника %s", capture.source)
            return capture
        finally:
            await context.close()
            await browser.close()


async def interactive_login(
    state_path: Path,
    *,
    url: str = DEFAULT_URL,
    timeout: float = LOGIN_TIMEOUT,
    on_message=None,
) -> Capture:
    """Открывает окно браузера, ждёт, пока пользователь войдёт, сохраняет сессию.

    Ни номер телефона, ни код из SMS программа не видит и не хранит: их
    получает браузер напрямую от 2ГИС. На диск ложатся только куки.

    Признак успеха — пойманный токен: он появляется ровно тогда, когда 2ГИС
    признал пользователя своим. Ждать нажатия Enter не нужно, окно закроется
    само.
    """
    say = on_message or (lambda message: None)
    async_playwright = _import_playwright()
    collector = TokenCollector()
    prepare_parent(state_path)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=False)
        except Exception as error:  # pragma: no cover — зависит от окружения
            raise _launch_failure(error) from error
        context = await browser.new_context(
            storage_state=str(state_path) if state_path.exists() else None
        )
        try:
            page = await context.new_page()
            _attach(page, collector)
            await page.add_init_script(f"({HOOK_JS})()")
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            say("Открылось окно браузера. Войди в свой аккаунт 2ГИС.")
            say("Окно закроется само, как только сессия будет готова.")

            capture = await _wait_for_token(page, collector, timeout)
            if capture is None:
                raise TokenNotFound(
                    f"за {timeout / 60:.0f} мин токен так и не появился.\n"
                    "Если вход выполнен, а окно не закрылось — обнови страницу 2gis.ru\n"
                    "в открытом окне: токен приезжает вместе с загрузкой карты."
                )

            await context.storage_state(path=str(state_path))
            harden(state_path)
            say(f"Сессия сохранена: {state_path}")
            return capture
        finally:
            await context.close()
            await browser.close()


def install_browser() -> int:
    """Скачивает Chromium для Playwright.

    Отдельная команда нужна тем, кто взял готовую сборку: там нет ни pip, ни
    возможности выполнить ``playwright install`` привычным способом.
    """
    import subprocess
    import sys

    if getattr(sys, "frozen", False):  # однофайловая сборка PyInstaller
        from playwright.__main__ import main as playwright_main

        saved = sys.argv
        sys.argv = ["playwright", "install", "chromium"]
        try:
            playwright_main()
        except SystemExit as exit_code:
            return int(exit_code.code or 0)
        finally:
            sys.argv = saved
        return 0

    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])
