"""Язык окна и его тексты.

Выбор языка — чистая функция от системы и окружения, поэтому проверяется на
всех трёх платформах сразу. Тексты проверяются на то, в чём переводы обычно и
ломаются: пропущенный ключ, разошедшиеся подстановки, ошибка, показанная
чужим текстом.
"""

from __future__ import annotations

import string

import pytest

from twogis_token.errors import (
    BrowserMissing,
    PageNotLoaded,
    PlaywrightMissing,
    SessionBusy,
    SessionExpired,
    SessionMissing,
    TokenNotFound,
)
from twogis_token.i18n import (
    DEFAULT,
    SUPPORTED,
    TEXTS,
    describe_error,
    language_from_tag,
    language_from_windows_langid,
    pick_language,
)

TOKEN = "0123456789abcdef0123456789abcdef01234567"

RUSSIAN_UI = 0x0419  # ru-RU
ENGLISH_UI = 0x0409  # en-US
UKRAINIAN_UI = 0x0422


class TestОбозначениеЛокали:
    @pytest.mark.parametrize("tag", ["ru_RU.UTF-8", "ru-RU", "ru", "Russian_Russia", "RU_ru"])
    def test_русский(self, tag):
        assert language_from_tag(tag) == "ru"

    @pytest.mark.parametrize("tag", ["en_US.UTF-8", "en", "English_United States", "de_DE"])
    def test_прочие_дают_английский(self, tag):
        """Других переводов нет, а английский понятнее прочих."""
        assert language_from_tag(tag) == "en"

    @pytest.mark.parametrize("tag", [None, "", "  ", "C", "POSIX", "C.UTF-8"])
    def test_ничего_не_говорит_о_языке(self, tag):
        assert language_from_tag(tag) is None


class TestLangidWindows:
    def test_русский(self):
        assert language_from_windows_langid(RUSSIAN_UI) == "ru"

    def test_английский(self):
        assert language_from_windows_langid(ENGLISH_UI) == "en"

    def test_другой_язык(self):
        assert language_from_windows_langid(UKRAINIAN_UI) == "en"

    def test_нет_данных(self):
        assert language_from_windows_langid(None) is None
        assert language_from_windows_langid(0) is None


class TestВыбор:
    def test_явная_просьба_важнее_всего(self):
        assert pick_language(override="ru", platform="win32", windows_langid=ENGLISH_UI) == "ru"
        assert pick_language(override="en", platform="linux", env={"LANG": "ru_RU.UTF-8"}) == "en"

    def test_windows_берёт_язык_интерфейса(self):
        assert pick_language(platform="win32", windows_langid=RUSSIAN_UI) == "ru"

    def test_windows_интерфейс_важнее_региональных_настроек(self):
        """Ровно машина автора: интерфейс английский, регион русский."""
        result = pick_language(
            platform="win32", windows_langid=ENGLISH_UI, fallback="Russian_Russia"
        )
        assert result == "en"

    def test_windows_не_слушает_переменные_git_bash(self):
        """LANG оставляет оболочка, и о языке системы он не говорит."""
        result = pick_language(
            platform="win32", windows_langid=ENGLISH_UI, env={"LANG": "ru_RU.UTF-8"}
        )
        assert result == "en"

    def test_linux_по_переменным(self):
        assert pick_language(platform="linux", env={"LANG": "ru_RU.UTF-8"}) == "ru"

    def test_linux_порядок_переменных(self):
        env = {"LC_ALL": "en_US.UTF-8", "LC_MESSAGES": "ru_RU.UTF-8", "LANG": "ru_RU.UTF-8"}
        assert pick_language(platform="linux", env=env) == "en"

    def test_linux_пропускает_бессодержательные(self):
        env = {"LC_ALL": "", "LC_MESSAGES": "C", "LANG": "ru_RU.UTF-8"}
        assert pick_language(platform="linux", env=env) == "ru"

    def test_запасной_источник(self):
        assert pick_language(platform="darwin", env={}, fallback="ru_RU") == "ru"

    def test_совсем_ничего_не_известно(self):
        assert pick_language(platform="linux", env={}) == DEFAULT


class TestТексты:
    def test_у_языков_одинаковые_ключи(self):
        """Забытый ключ в переводе — это KeyError посреди работы окна."""
        образец = set(TEXTS[DEFAULT])
        for язык in SUPPORTED:
            assert set(TEXTS[язык]) == образец, язык

    def test_подстановки_совпадают(self):
        """Иначе .format() упадёт только на одном языке — и только у тех, кто на нём."""

        def поля(text):
            return {name for _, name, _, _ in string.Formatter().parse(text) if name}

        for key, text in TEXTS[DEFAULT].items():
            for язык in SUPPORTED:
                assert поля(TEXTS[язык][key]) == поля(text), (язык, key)

    def test_переводы_не_пустые(self):
        for язык in SUPPORTED:
            for key, text in TEXTS[язык].items():
                assert text.strip(), (язык, key)


class TestОшибкиВОкне:
    @pytest.mark.parametrize(
        ("error", "key"),
        [
            (PageNotLoaded("…"), "error_page_not_loaded"),
            (SessionExpired("…"), "error_session_expired"),
            (SessionMissing("…"), "error_session_missing"),
            (SessionBusy("…"), "error_session_busy"),
            (BrowserMissing("…"), "error_browser_missing"),
            (PlaywrightMissing("…"), "error_playwright_missing"),
            (TokenNotFound("…"), "error_login_timeout"),
        ],
    )
    def test_каждой_ошибке_свой_текст(self, error, key):
        for язык in SUPPORTED:
            assert describe_error(error, язык) == TEXTS[язык][key]

    def test_незагрузившаяся_страница_не_зовёт_входить(self):
        """PageNotLoaded — частный случай SessionExpired, и порядок проверки важен."""
        текст = describe_error(PageNotLoaded("…"), "ru")
        assert текст != TEXTS["ru"]["error_session_expired"]

    def test_консольные_советы_в_окно_не_попадают(self):
        """У человека в окне есть кнопка «Войти», команда ему ни к чему."""
        ошибка = SessionMissing("no saved session.\nSign in once:  2gis-token login")
        assert "2gis-token" not in describe_error(ошибка, "en")

    def test_непредвиденная_ошибка_показывается(self):
        текст = describe_error(RuntimeError("диск переполнен"), "ru")
        assert "диск переполнен" in текст

    def test_токен_не_попадает_на_экран(self):
        текст = describe_error(RuntimeError(f"сбой на ?token={TOKEN}"), "en")
        assert TOKEN not in текст

    def test_ошибка_без_текста(self):
        assert "RuntimeError" in describe_error(RuntimeError(), "en")
