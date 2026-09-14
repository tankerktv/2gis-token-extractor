"""Единственное место, где есть браузер.

Токен снимается **перехватом сетевой активности страницы**, а не выковыриванием
из внутреннего состояния приложения. Это принципиально: внутреннее устройство
фронтенда 2ГИС меняется когда угодно, а токен в параметре запроса — это их
протокол, он живёт дольше.

Ловим тремя сетями, от надёжной к запасной:

1. ``page.on("websocket")`` — событие Playwright. Тот самый ``user/ws``.
2. ``page.on("request")``   — адреса и заголовки обычных запросов.
3. Подмена ``WebSocket`` и ``fetch`` скриптом на странице — на случай, если
   что-то пройдёт мимо первых двух.

Замечено при первой же проверке на живом аккаунте: токен приезжает не только
сокетом. В наблюдавшемся заходе сокет не открывался вовсе, а токен нашёлся в
параметре обычного запроса. Поэтому вторая сеть — не запасная роскошь, а
рабочий путь.

Два наблюдения, оплаченных временем; оба не очевидны и оба легко потерять
при переписывании.

**Headless выдаёт себя строкой User-Agent, и 2ГИС ему приложение не отдаёт.**
Playwright по умолчанию представляется как ``HeadlessChrome/124...``. С такой
строкой страница делает пятнадцать запросов и замирает: ни карты, ни
авторизации, ни токена. Стоит подставить обычный ``Chrome`` — и запросов
становится под сотню, приложение живёт. Поэтому UA берётся у самого браузера
и чинится заменой одного слова: подставлять выдуманную версию нельзя, она
разойдётся с настоящей при первом же обновлении Playwright.

**Одной попытки мало.** Токен приезжает вместе с загрузкой приложения, а оно
иногда не доезжает. Поэтому ``get`` не сдаётся сразу: страница перезагружается
и ждёт снова. Отпущенный срок при этом делится между попытками, а не
удваивается — ``--timeout`` остаётся полным бюджетом, на который можно
рассчитывать в расписании.

**Куки обновляются при визите, и не сохранить их — значит потерять сессию.**
Раньше состояние записывалось только при удаче. Один заход без сохранения — и
на диске оставались прежние куки, а вход приходилось повторять, хотя он был
совсем свежий. Теперь состояние пишется всегда, в ``finally``: даже когда
токен не пойман, обновлённые куки уезжают на диск.

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
    PageNotLoaded,
    PlaywrightMissing,
    SessionExpired,
    SessionMissing,
    TokenNotFound,
)
from .state import SessionLock, commit_state, prepare_parent, temp_state_path
from .tokens import Found, TokenCollector

log = logging.getLogger(__name__)

#: Страница, на которой приложение 2ГИС запрашивает токен.
DEFAULT_URL = "https://2gis.ru/"

DEFAULT_TIMEOUT = 60.0
LOGIN_TIMEOUT = 600.0

#: Сколько ждать источник понадёжнее после первой находки.
#:
#: Перехватчик на странице срабатывает на доли секунды раньше события
#: websocket, и без этой отсрочки возвращался бы он — а он же может ухватить
#: посторонний sha1. Пауза короткая: на живой странице события идут подряд.
GRACE = 2.0

#: Ниже этого числа запросов приложение считается незапустившимся.
#:
#: Замерено: не запустившаяся страница делает 14-15 запросов, живая — около
#: сотни. Порог посередине, к точному числу не привязываемся.
BOOT_REQUESTS = 40

#: Сколько раз пробовать. Вторая попытка — просто перезагрузка страницы.
ATTEMPTS = 2

#: Короче этого срока попытка бессмысленна: приложению нужно успеть завестись.
MIN_ATTEMPT = 15.0

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


class Traffic:
    """Сколько запросов увидела страница.

    Нужен ровно для одного: отличить «приложение не запустилось» от
    «запустилось, но токена не дало». Лечится это по-разному, и сваливать оба
    случая в одно сообщение — значит гнать человека логиниться заново там,
    где вход ни при чём.
    """

    def __init__(self) -> None:
        self.requests = 0


def _import_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:  # pragma: no cover — зависит от окружения
        raise PlaywrightMissing(
            "Playwright is not installed.\n"
            "  pip install playwright\n"
            "  2gis-token install-browser"
        ) from error
    return async_playwright


def _launch_failure(error: Exception) -> Exception:
    """Отличает «браузер не скачан» от прочих бед запуска."""
    text = str(error)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return BrowserMissing(
            "Playwright is installed, but it has no browser yet. Download one:\n"
            "  2gis-token install-browser"
        )
    return error


def honest_user_agent(user_agent: str | None) -> str | None:
    """Убирает из строки браузера признание в том, что он без окна.

    Подменяется одно слово, всё остальное — настоящее: версия, платформа,
    порядок частей. Выдуманная строка разошлась бы с настоящим браузером и
    была бы заметнее, чем исходная. ``None`` означает «чинить нечего».
    """
    if not user_agent or "Headless" not in user_agent:
        return None
    return user_agent.replace("HeadlessChrome", "Chrome").replace("Headless", "")


async def _browser_user_agent(browser) -> str | None:
    """Спрашивает у браузера его строку и чинит её, если он headless."""
    context = await browser.new_context()
    try:
        page = await context.new_page()
        return honest_user_agent(await page.evaluate("navigator.userAgent"))
    except Exception:  # pragma: no cover — не повод падать, останемся как есть
        return None
    finally:
        await context.close()


def _attach(page, collector: TokenCollector, traffic: Traffic) -> None:
    """Подписывается на сетевые события страницы."""
    page.on("websocket", lambda ws: collector.offer_websocket_url(ws.url))

    def on_request(request) -> None:
        traffic.requests += 1
        collector.offer_request_url(request.url)
        try:
            collector.offer_headers(request.headers)
        except Exception:  # заголовки могут быть уже недоступны — не повод падать
            pass

    page.on("request", on_request)


def enough(found: Found | None, waited: float, grace: float = GRACE) -> bool:
    """Хватит ли найденного или стоит подождать источник понадёжнее.

    Адрес сокета сомнений не вызывает — с ним останавливаемся сразу. Всё
    остальное держим ``grace`` секунд: за это время может приехать тот самый
    ``user/ws``, и он вытеснит случайную находку.
    """
    if found is None:
        return False
    if found.source == "websocket":
        return True
    return waited >= grace


def login_command(profile: str | None = None) -> str:
    """Команда входа, которую стоит посоветовать именно этому человеку.

    Без профиля совет ``2gis-token login`` верен, а с профилем — уже нет:
    выполнив его, человек войдёт в другую сессию и снова получит тот же
    отказ. Подсказка должна быть исполнимой как есть.
    """
    return "2gis-token login" + (f" --profile {profile}" if profile else "")


def plan_attempts(
    timeout: float,
    attempts: int = ATTEMPTS,
    minimum: float = MIN_ATTEMPT,
) -> list[float]:
    """Делит отпущенный срок между попытками.

    Токен приезжает вместе с загрузкой приложения, и приложение иногда не
    доезжает: то ответ подвис, то скрипт не выполнился. Перезагрузка страницы
    стоит секунду и часто спасает — сдаваться после одной попытки жалко.

    Срок при этом **делится, а не добавляется**: ``--timeout`` остаётся полным
    бюджетом. Иначе ключ означал бы одно при удаче и вдвое больше при неудаче,
    а на него полагаются скрипты и расписания.

    Делить имеет смысл, только если каждой попытке достаётся осмысленный срок.
    При ``--timeout 10`` две попытки по пять секунд хуже одной десятисекундной:
    не успеет ни та, ни другая.
    """
    if attempts < 2 or timeout / attempts < minimum:
        return [timeout]
    return [timeout / attempts] * attempts


def diagnose(requests: int, timeout: float, profile: str | None = None) -> str:
    """Объясняет, почему токена нет, — по тому, ожила ли страница вообще."""
    if requests < BOOT_REQUESTS:
        return (
            f"the page barely loaded: {requests} requests in {timeout:.0f} s.\n"
            "The 2GIS app never started, so there was no token to catch.\n"
            "Check that 2gis.ru is reachable, then try it with a window:\n"
            "  2gis-token get --headed"
        )
    return (
        f"the app loaded ({requests} requests), but none of them carried a token.\n"
        "That usually means the session has expired — sign in again:\n"
        f"  {login_command(profile)}"
    )


def expired_error(requests: int, timeout: float, profile: str | None = None) -> SessionExpired:
    """Какую ошибку поднять, когда токен так и не появился.

    Текст для консоли одинаково подробный в обоих случаях. Тип же разный: окно
    не читает текст, а смотрит на тип, и при незагрузившейся странице не
    должно звать человека входить заново.
    """
    text = diagnose(requests, timeout, profile)
    if requests < BOOT_REQUESTS:
        return PageNotLoaded(text)
    return SessionExpired(text)


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


async def _save_state(context, state_path: Path) -> None:
    """Записывает куки на диск. Вызывается всегда, даже когда токен не пойман.

    2ГИС обновляет куки при визите. Если не сохранить обновлённые, на диске
    останутся прежние — и следующий заход придёт с устаревшей сессией.
    """
    temp = temp_state_path(state_path)
    try:
        prepare_parent(state_path)
        await context.storage_state(path=str(temp))
        commit_state(temp, state_path)
    except Exception as error:  # pragma: no cover — диск или закрытый контекст
        log.warning("could not save the session to %s: %s", state_path, error)
        try:
            temp.unlink()
        except OSError:
            pass


async def capture_token(
    state_path: Path,
    *,
    url: str = DEFAULT_URL,
    timeout: float = DEFAULT_TIMEOUT,
    headless: bool = True,
    profile: str | None = None,
) -> Capture:
    """Заходит на 2ГИС с сохранённой сессией и забирает токен."""
    if not state_path.exists():
        raise SessionMissing(
            f"no saved session ({state_path}).\n"
            f"Sign in once:  {login_command(profile)}"
        )

    async_playwright = _import_playwright()
    collector = TokenCollector()
    traffic = Traffic()

    async with SessionLock(state_path), async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless)
        except Exception as error:  # pragma: no cover — зависит от окружения
            raise _launch_failure(error) from error

        user_agent = await _browser_user_agent(browser) if headless else None
        context = await browser.new_context(
            storage_state=str(state_path),
            **({"user_agent": user_agent} if user_agent else {}),
        )
        try:
            page = await context.new_page()
            _attach(page, collector, traffic)
            await page.add_init_script(f"({HOOK_JS})()")

            budgets = plan_attempts(timeout)
            for number, budget in enumerate(budgets, start=1):
                if number == 1:
                    await page.goto(url, wait_until="domcontentloaded", timeout=budget * 1000)
                else:
                    log.info("no token yet, reloading (attempt %d of %d)", number, len(budgets))
                    await page.reload(wait_until="domcontentloaded", timeout=budget * 1000)

                capture = await _wait_for_token(page, collector, budget)
                if capture is not None:
                    log.info("token captured from %s", capture.source)
                    return capture

            raise expired_error(traffic.requests, timeout, profile)
        finally:
            await _save_state(context, state_path)
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
    traffic = Traffic()
    prepare_parent(state_path)

    async with SessionLock(state_path), async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=False)
        except Exception as error:  # pragma: no cover — зависит от окружения
            raise _launch_failure(error) from error
        context = await browser.new_context(
            storage_state=str(state_path) if state_path.exists() else None
        )
        try:
            page = await context.new_page()
            _attach(page, collector, traffic)
            await page.add_init_script(f"({HOOK_JS})()")
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            say("A browser window is open. Sign in to your 2GIS account.")
            say("It will close by itself once the session is ready.")

            capture = await _wait_for_token(page, collector, timeout)
            if capture is None:
                raise TokenNotFound(
                    f"no token appeared in {timeout / 60:.0f} min.\n"
                    "If you did sign in and the window is still open, reload 2gis.ru\n"
                    "there: the token arrives together with the map."
                )
            say(f"Session saved: {state_path}")
            return capture
        finally:
            await _save_state(context, state_path)
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
