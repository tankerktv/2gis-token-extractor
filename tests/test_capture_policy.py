"""Когда прекращать ждать токен.

Сам браузер тестами не покрыт — его поведение задаёт 2ГИС, и проверка здесь
проверяла бы Playwright. А вот правило остановки — наше, и ошибка в нём тихо
возвращает случайный sha1 вместо токена.

Модуль ``browser`` импортируется без Playwright: тот подтягивается только в
момент запуска браузера.
"""

from __future__ import annotations

from twogis_token.browser import GRACE, enough
from twogis_token.tokens import Found

TOKEN = "0123456789abcdef0123456789abcdef01234567"


def test_ничего_не_найдено():
    assert not enough(None, waited=100.0)


def test_адрес_сокета_принимается_сразу():
    assert enough(Found(TOKEN, "websocket"), waited=0.0)


def test_сомнительная_находка_ждёт_надёжную():
    """Перехватчик на странице срабатывает раньше события websocket."""
    assert not enough(Found(TOKEN, "text"), waited=0.0)
    assert not enough(Found(TOKEN, "header"), waited=GRACE / 2)


def test_но_не_ждёт_вечно():
    assert enough(Found(TOKEN, "text"), waited=GRACE)
    assert enough(Found(TOKEN, "query"), waited=GRACE + 1)
