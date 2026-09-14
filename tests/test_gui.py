"""Окно: собирается ли, отзывается ли на кнопки, не показывает ли токен зря.

Браузер здесь подменяется, а работа выполняется сразу, в том же потоке, —
проверяется склейка окна с уже проверенными модулями, а не Playwright.

Где открыть окно нельзя (Linux без экрана в CI), тесты пропускаются: там
окно всё равно никто не откроет.
"""

from __future__ import annotations

import pytest

from twogis_token import auth_api, browser, gui
from twogis_token.auth_api import CheckResult
from twogis_token.errors import BrowserMissing, PageNotLoaded
from twogis_token.i18n import TEXTS

tk = pytest.importorskip("tkinter")

TOKEN = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(scope="module")
def tk_root():
    """Один интерпретатор Tk на все тесты.

    Новый ``tk.Tk()`` на каждый тест на Windows изредка спотыкается о загрузку
    собственных скриптов — ``tcl_findLibrary``, ``ttk/panedwindow.tcl`` — и
    тест мерцает без всякой вины окна. У живого пользователя Tk один на
    процесс, так что это беда только тестов; лечится тем же, что у него:
    один Tk, а окна — отдельные.
    """
    try:
        interpreter = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"окно открыть негде: {error}")
    interpreter.withdraw()
    yield interpreter
    interpreter.destroy()


@pytest.fixture
def root(tk_root):
    window = tk.Toplevel(tk_root)
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture
def сессия(tmp_path):
    path = tmp_path / "storage_state.json"
    path.write_text("{}", encoding="utf-8")
    return path


def окно(root, state_path, language="en"):
    return gui.TokenWindow(root, language=language, state_path=state_path, launch=gui.run_now)


@pytest.fixture
def захват_удаётся(monkeypatch):
    async def capture(state_path, **kwargs):
        return browser.Capture(TOKEN, "query")

    monkeypatch.setattr(browser, "capture_token", capture)
    monkeypatch.setattr(
        auth_api,
        "check_token",
        lambda token, **kw: CheckResult(alive=True, detail="alive", status=200, account="Дмитрий"),
    )


class TestСостояние:
    def test_без_сессии_зовёт_войти(self, root, tmp_path):
        w = окно(root, tmp_path / "нет.json")
        assert w.status.get() == TEXTS["en"]["status_no_session"]

    def test_с_сессией_предлагает_токен(self, root, сессия):
        w = окно(root, сессия)
        assert w.status.get() == TEXTS["en"]["status_session"]

    def test_говорит_на_выбранном_языке(self, root, сессия):
        w = окно(root, сессия, language="ru")
        assert w.get_button.cget("text") == "Получить токен"
        assert root.title() == TEXTS["ru"]["title"]

    def test_без_токена_копировать_нечего(self, root, сессия):
        w = окно(root, сессия)
        assert str(w.copy_button.cget("state")) == "disabled"
        assert str(w.show_button.cget("state")) == "disabled"

    def test_кнопка_установки_браузера_спрятана(self, root, сессия):
        """Большинству она не нужна никогда — и маячить незачем."""
        assert not окно(root, сессия).install_button_visible()


class TestПолучениеТокена:
    def test_токен_скрыт_пока_не_попросят(self, root, сессия, захват_удаётся):
        w = окно(root, сессия)
        w.get_token()
        assert w.token == TOKEN
        assert TOKEN not in w.token_text.get()
        assert w.token_text.get() == gui.MASK * len(TOKEN)

    def test_имя_аккаунта_в_состоянии(self, root, сессия, захват_удаётся):
        w = окно(root, сессия)
        w.get_token()
        assert "Дмитрий" in w.status.get()

    def test_показать_и_скрыть(self, root, сессия, захват_удаётся):
        w = окно(root, сессия)
        w.get_token()
        w.toggle_reveal()
        assert w.token_text.get() == TOKEN
        assert w.show_button.cget("text") == TEXTS["en"]["button_hide"]
        w.toggle_reveal()
        assert TOKEN not in w.token_text.get()

    def test_копирование_кладёт_токен_в_буфер(self, root, сессия, захват_удаётся):
        w = окно(root, сессия)
        w.get_token()
        w.copy_token()
        assert root.clipboard_get() == TOKEN
        assert w.status.get() == TEXTS["en"]["status_copied"]

    def test_после_токена_кнопки_доступны(self, root, сессия, захват_удаётся):
        w = окно(root, сессия)
        w.get_token()
        assert not w.busy
        assert str(w.copy_button.cget("state")) == "normal"
        assert str(w.get_button.cget("state")) == "normal"

    def test_без_сети_до_профиля_токен_всё_равно_отдаётся(self, root, сессия, monkeypatch):
        async def capture(state_path, **kwargs):
            return browser.Capture(TOKEN, "query")

        monkeypatch.setattr(browser, "capture_token", capture)
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=False, detail="нет сети", reachable=False),
        )
        w = окно(root, сессия)
        w.get_token()
        assert w.token == TOKEN
        assert w.status.get() == TEXTS["en"]["status_got_plain"]


class TestПовторнаяПопытка:
    def test_новая_попытка_снова_прячет_токен(self, root, сессия, захват_удаётся, monkeypatch):
        """Нашлось на снимке: раскрытый токен висел рядом с ошибкой новой попытки."""
        w = окно(root, сессия)
        w.get_token()
        w.toggle_reveal()
        assert w.token_text.get() == TOKEN

        async def capture(state_path, **kwargs):
            raise BrowserMissing("no browser")

        monkeypatch.setattr(browser, "capture_token", capture)
        w.get_token()
        assert w.token == TOKEN  # прежний токен рабочий и не выброшен
        assert TOKEN not in w.token_text.get()  # но снова спрятан
        assert w.show_button.cget("text") == TEXTS["en"]["button_show"]


class TestОшибки:
    def test_нет_браузера_появляется_кнопка_установки(self, root, сессия, monkeypatch):
        async def capture(state_path, **kwargs):
            raise BrowserMissing("no browser")

        monkeypatch.setattr(browser, "capture_token", capture)
        w = окно(root, сессия)
        w.get_token()
        assert w.install_button_visible()
        assert w.status.get() == TEXTS["en"]["error_browser_missing"]
        assert not w.busy

    def test_незагрузившаяся_страница(self, root, сессия, monkeypatch):
        async def capture(state_path, **kwargs):
            raise PageNotLoaded("barely loaded")

        monkeypatch.setattr(browser, "capture_token", capture)
        w = окно(root, сессия, language="ru")
        w.get_token()
        assert w.status.get() == TEXTS["ru"]["error_page_not_loaded"]
        assert w.token is None

    def test_установка_браузера(self, root, сессия, monkeypatch):
        monkeypatch.setattr(browser, "install_browser", lambda: 0)
        w = окно(root, сессия)
        w.install_button.grid()
        w.install_browser()
        assert not w.install_button_visible()
        assert w.status.get() == TEXTS["en"]["status_installed"]

    def test_неудачная_установка_называет_код(self, root, сессия, monkeypatch):
        monkeypatch.setattr(browser, "install_browser", lambda: 7)
        w = окно(root, сессия)
        w.install_browser()
        assert "7" in w.status.get()
