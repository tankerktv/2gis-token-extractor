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

import os
import sys
from pathlib import Path

import pytest

from twogis_token.browser import (
    ATTEMPTS,
    BOOT_REQUESTS,
    ENV_BROWSERS_PATH,
    GRACE,
    MIN_ATTEMPT,
    browsers_path_to_pin,
    diagnose,
    enough,
    honest_user_agent,
    login_command,
    pin_browsers_path,
    plan_attempts,
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


class TestДелениеСрока:
    """Вторая попытка — перезагрузка страницы. Она не должна стоить лишнего времени."""

    def test_срок_делится_а_не_добавляется(self):
        """Иначе --timeout значил бы одно при удаче и вдвое больше при неудаче."""
        assert sum(plan_attempts(60)) == 60

    def test_попыток_столько_сколько_обещано(self):
        assert len(plan_attempts(60)) == ATTEMPTS

    def test_поровну(self):
        assert plan_attempts(60) == [30.0, 30.0]

    def test_короткий_срок_не_дробится(self):
        """Две попытки по пять секунд хуже одной десятисекундной: не успеет ни та, ни та."""
        assert plan_attempts(10) == [10]

    def test_граница(self):
        предел = MIN_ATTEMPT * ATTEMPTS
        assert len(plan_attempts(предел)) == ATTEMPTS
        assert plan_attempts(предел - 1) == [предел - 1]

    def test_одна_попытка_по_просьбе(self):
        assert plan_attempts(60, attempts=1) == [60]

    def test_три_попытки(self):
        assert plan_attempts(90, attempts=3) == [30.0, 30.0, 30.0]


HOME = Path("/дом/пользователь")


class TestГдеБраузерВСборке:
    """Первые три выпуска сборок не находили браузер, который сами же ставили.

    Playwright в сборке PyInstaller ищет браузер внутри программы, во временной
    папке распаковки, а install-browser кладёт его в кэш пользователя.
    """

    def test_сборка_смотрит_в_кэш_пользователя(self):
        путь = browsers_path_to_pin(
            frozen=True, env={"LOCALAPPDATA": "/local"}, system="Windows", home=HOME
        )
        assert путь == str(Path("/local") / "ms-playwright")

    def test_windows_без_localappdata(self):
        путь = browsers_path_to_pin(frozen=True, env={}, system="Windows", home=HOME)
        assert путь == str(HOME / "AppData" / "Local" / "ms-playwright")

    def test_macos(self):
        путь = browsers_path_to_pin(frozen=True, env={}, system="Darwin", home=HOME)
        assert путь == str(HOME / "Library" / "Caches" / "ms-playwright")

    def test_linux_по_умолчанию(self):
        путь = browsers_path_to_pin(frozen=True, env={}, system="Linux", home=HOME)
        assert путь == str(HOME / ".cache" / "ms-playwright")

    def test_linux_уважает_xdg(self):
        путь = browsers_path_to_pin(
            frozen=True, env={"XDG_CACHE_HOME": "/кэш"}, system="Linux", home=HOME
        )
        assert путь == str(Path("/кэш") / "ms-playwright")

    def test_не_сборку_не_трогаем(self):
        """Поставленной через pip программе Playwright и так ищет в кэше."""
        assert browsers_path_to_pin(frozen=False, env={}, system="Windows", home=HOME) is None

    @pytest.mark.parametrize("своё", ["/мои/браузеры", "0"])
    def test_путь_заданный_человеком_не_трогаем(self, своё):
        env = {ENV_BROWSERS_PATH: своё}
        assert browsers_path_to_pin(frozen=True, env=env, system="Windows", home=HOME) is None

    def test_в_сборке_путь_выставляется_до_работы(self, monkeypatch):
        """Выставить нужно раньше Playwright: он кладёт свой «0» через setdefault."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delenv(ENV_BROWSERS_PATH, raising=False)
        pin_browsers_path()
        assert os.environ[ENV_BROWSERS_PATH].endswith("ms-playwright")

    def test_без_сборки_окружение_не_меняется(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delenv(ENV_BROWSERS_PATH, raising=False)
        pin_browsers_path()
        assert ENV_BROWSERS_PATH not in os.environ


class TestПодсказкаВхода:
    def test_без_профиля(self):
        assert login_command() == "2gis-token login"

    def test_с_профилем(self):
        """Иначе человек выполнит совет и войдёт не в ту сессию."""
        assert login_command("работа") == "2gis-token login --profile работа"

    def test_диагноз_повторяет_профиль(self):
        assert "--profile работа" in diagnose(120, timeout=60, profile="работа")
