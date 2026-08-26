"""Дымовой тест браузерной обвязки: настоящий Chromium на подставной странице.

Всё остальное в этом каталоге — чистая логика без браузера. Здесь наоборот:
проверяется именно склейка с Playwright, то место, где ошибка не видна ни
линтеру, ни тестам правил. Ровно там за время работы и обнаружились обе
неприятности — подписка на события и сохранение куков.

Настоящий 2ГИС не участвует: тест обязан проходить в CI без аккаунта и без
сети. Подставная страница лежит рядом, в ``pages/``.

Тесты помечены ``browser`` и по умолчанию **не гоняются**: им нужен скачанный
Chromium, а это сотни мегабайт. В CI под них отдельная джоба:

    pytest -m browser
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from twogis_token import browser
from twogis_token.errors import SessionBusy, SessionExpired

pytestmark = pytest.mark.browser

TOKEN = "0123456789abcdef0123456789abcdef01234567"
PAGE = (Path(__file__).parent / "pages" / "fake_2gis.html").resolve().as_uri()
PAGE_SECOND_TRY = (
    (Path(__file__).parent / "pages" / "fake_2gis_second_try.html").resolve().as_uri()
)


@pytest.fixture
def сессия(tmp_path) -> Path:
    """Пустая, но правильная по форме сессия — Playwright такую принимает."""
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    return path


@pytest.fixture
def пустая_страница(tmp_path) -> str:
    path = tmp_path / "пусто.html"
    path.write_text("<!doctype html><title>ничего</title><p>тут пусто", encoding="utf-8")
    return path.resolve().as_uri()


def test_ловит_токен_и_предпочитает_сокет(сессия):
    """Токен есть и в запросе, и в сокете. Победить обязан сокет."""
    capture = asyncio.run(browser.capture_token(сессия, url=PAGE, timeout=30))
    assert capture.token == TOKEN
    assert capture.source == "websocket"


def test_посторонние_sha1_не_принимаются(сессия):
    """На странице три чужих отпечатка по сорок знаков — ни один не должен пройти."""
    capture = asyncio.run(browser.capture_token(сессия, url=PAGE, timeout=30))
    assert not capture.token.startswith(("a", "b", "c"))


def test_сессия_сохраняется_даже_когда_токена_нет(сессия, пустая_страница):
    """Главное исправление: неудачный заход не должен стоить человеку новой SMS."""
    сессия.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    with pytest.raises(SessionExpired):
        asyncio.run(browser.capture_token(сессия, url=пустая_страница, timeout=5))

    # файл на месте и остался разбираемым, а не обрубком
    assert json.loads(сессия.read_text(encoding="utf-8")) is not None


def test_вторая_попытка_спасает(сессия):
    """Страница отдаёт токен только после перезагрузки.

    Если программа сдастся после первой попытки, тест покраснеет — снаружи
    перезагрузку иначе не увидеть.
    """
    capture = asyncio.run(browser.capture_token(сессия, url=PAGE_SECOND_TRY, timeout=40))
    assert capture.token == TOKEN


def test_одной_попытки_на_такой_странице_не_хватает(сессия):
    """Обратная сторона: при коротком сроке попытка одна, и токена не будет."""
    with pytest.raises(SessionExpired):
        asyncio.run(browser.capture_token(сессия, url=PAGE_SECOND_TRY, timeout=8))


def test_замок_не_пускает_второй_заход(сессия):
    """Два браузера на одной сессии — гонка, в которой теряются свежие куки."""
    from twogis_token.state import SessionLock

    with SessionLock(сессия):
        with pytest.raises(SessionBusy):
            asyncio.run(browser.capture_token(сессия, url=PAGE, timeout=10))


def test_без_сессии_просит_войти(tmp_path):
    from twogis_token.errors import SessionMissing

    with pytest.raises(SessionMissing):
        asyncio.run(browser.capture_token(tmp_path / "нет.json", url=PAGE, timeout=5))
