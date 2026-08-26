"""Правила захвата: когда прекращать ждать, чем представляться, что сказать при неудаче.

Сам браузер тестами не покрыт — его поведение задаёт 2ГИС, и проверка здесь
проверяла бы Playwright. А вот эти три правила наши, и каждое куплено опытом:
ошибка в первом тихо возвращает случайный sha1 вместо токена, во втором —
приложение 2ГИС вообще не запускается, в третьем — человека отправляют
логиниться заново там, где вход ни при чём.

Модуль ``browser`` импортируется без Playwright: тот подтягивается только в
момент запуска браузера.
"""

from __future__ import annotations

from twogis_token.browser import (
    BOOT_REQUESTS,
    GRACE,
    diagnose,
    enough,
    honest_user_agent,
    login_command,
)
from twogis_token.tokens import Found

TOKEN = "0123456789abcdef0123456789abcdef01234567"

HEADLESS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) HeadlessChrome/124.0.6367.29 Safari/537.36"
)


class TestКогдаОстанавливаться:
    def test_ничего_не_найдено(self):
        assert not enough(None, waited=100.0)

    def test_адрес_сокета_принимается_сразу(self):
        assert enough(Found(TOKEN, "websocket"), waited=0.0)

    def test_сомнительная_находка_ждёт_надёжную(self):
        """Перехватчик на странице срабатывает раньше события websocket."""
        assert not enough(Found(TOKEN, "text"), waited=0.0)
        assert not enough(Found(TOKEN, "header"), waited=GRACE / 2)

    def test_но_не_ждёт_вечно(self):
        assert enough(Found(TOKEN, "text"), waited=GRACE)
        assert enough(Found(TOKEN, "query"), waited=GRACE + 1)


class TestСтрокаБраузера:
    """2ГИС не отдаёт приложение браузеру, который признаётся, что он без окна."""

    def test_headless_чинится(self):
        fixed = honest_user_agent(HEADLESS_UA)
        assert "Headless" not in fixed
        assert "Chrome/124.0.6367.29" in fixed

    def test_остальное_остаётся_настоящим(self):
        """Версию и платформу не выдумываем — иначе разойдёмся с самим браузером."""
        fixed = honest_user_agent(HEADLESS_UA)
        assert fixed == HEADLESS_UA.replace("HeadlessChrome", "Chrome")

    def test_честной_строке_чинить_нечего(self):
        honest = HEADLESS_UA.replace("HeadlessChrome", "Chrome")
        assert honest_user_agent(honest) is None

    def test_пусто(self):
        assert honest_user_agent(None) is None
        assert honest_user_agent("") is None


class TestОбъяснениеНеудачи:
    def test_страница_не_ожила(self):
        """Пятнадцать запросов — приложение не запустилось, вход ни при чём."""
        text = diagnose(15, timeout=60)
        assert "barely loaded" in text
        assert "--headed" in text
        assert "login" not in text

    def test_страница_ожила_но_токена_нет(self):
        text = diagnose(120, timeout=60)
        assert "login" in text
        assert "--headed" not in text

    def test_число_запросов_видно_человеку(self):
        assert "120" in diagnose(120, timeout=60)
        assert "15" in diagnose(15, timeout=60)

    def test_граница(self):
        assert "barely loaded" in diagnose(BOOT_REQUESTS - 1, timeout=60)
        assert "login" in diagnose(BOOT_REQUESTS, timeout=60)


class TestПодсказкаВхода:
    def test_без_профиля(self):
        assert login_command() == "2gis-token login"

    def test_с_профилем(self):
        """Иначе человек выполнит совет и войдёт не в ту сессию."""
        assert login_command("работа") == "2gis-token login --profile работа"

    def test_диагноз_повторяет_профиль(self):
        assert "--profile работа" in diagnose(120, timeout=60, profile="работа")
