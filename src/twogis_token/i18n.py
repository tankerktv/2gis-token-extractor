"""Язык окна и его тексты.

Консоль говорит по-английски всегда: её читают скрипты и те, кто их пишет.
Окно — для людей попроще, и почти все пользователи 2ГИС русскоязычные, поэтому
окно говорит на языке системы.

**Что считать языком системы** — вопрос не праздный, и на машине автора ответ
оказался неочевидным: интерфейс Windows английский, а региональные настройки
русские. Берётся язык интерфейса — так же поступают сама Windows, браузеры и
офис. Региональные настройки отвечают за формат дат и чисел, а не за надписи.
Кому нужно иначе — переменная ``TWOGIS_TOKEN_LANG`` или ключ ``--lang``.

**На Windows переменные LANG и LC_* не смотрим вовсе.** Их оставляют Git Bash и
WSL, и говорят они о настройках той оболочки, а не системы. Тот же урок, что с
XDG_CONFIG_HOME в state.py.

Модуль не знает про tkinter и потому проверяется тестами где угодно, в том
числе на машинах без экрана.
"""

from __future__ import annotations

import locale
import os
import sys
from collections.abc import Mapping

from .errors import (
    BrowserMissing,
    PageNotLoaded,
    PlaywrightMissing,
    SessionBusy,
    SessionExpired,
    SessionMissing,
    TokenNotFound,
)
from .tokens import redact

ENV_LANG = "TWOGIS_TOKEN_LANG"
SUPPORTED = ("en", "ru")
DEFAULT = "en"

#: Основной язык живёт в младших десяти битах LANGID Windows; у русского это 0x19.
WINDOWS_PRIMARY_MASK = 0x3FF
WINDOWS_PRIMARY_RUSSIAN = 0x19

#: Порядок, в котором POSIX-программы ищут язык сообщений.
POSIX_LOCALE_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")


def language_from_tag(tag: str | None) -> str | None:
    """Язык из обозначения локали: ``ru_RU.UTF-8``, ``ru-RU``, ``Russian_Russia``.

    ``None`` значит, что обозначение о языке ничего не говорит (``C``, ``POSIX``,
    пусто), и надо смотреть следующий источник. Любой язык, кроме русского,
    даёт английский: других переводов нет, а английский понятнее прочих.
    """
    if not tag:
        return None
    cleaned = tag.strip().lower()
    if not cleaned or cleaned in {"c", "posix"} or cleaned.startswith(("c.", "posix.")):
        return None
    if cleaned.startswith(("ru", "russian")):
        return "ru"
    return "en"


def language_from_windows_langid(langid: int | None) -> str | None:
    """Язык по LANGID Windows — числу, которое отдаёт GetUserDefaultUILanguage."""
    if not langid:
        return None
    return "ru" if langid & WINDOWS_PRIMARY_MASK == WINDOWS_PRIMARY_RUSSIAN else "en"


def pick_language(
    *,
    override: str | None = None,
    platform: str = "",
    windows_langid: int | None = None,
    env: Mapping[str, str] | None = None,
    fallback: str | None = None,
) -> str:
    """Выбирает язык окна.

    Порядок: явная просьба, потом язык интерфейса системы (на Windows —
    LANGID, на прочих — переменные локали), потом то, что знает ``locale``,
    и в самом конце английский.
    """
    explicit = language_from_tag(override)
    if explicit:
        return explicit

    if platform == "win32":
        from_system = language_from_windows_langid(windows_langid)
        if from_system:
            return from_system
    else:
        for name in POSIX_LOCALE_VARS:
            from_env = language_from_tag((env or {}).get(name))
            if from_env:
                return from_env

    return language_from_tag(fallback) or DEFAULT


def detect_language(override: str | None = None) -> str:
    """Язык окна на этой машине."""
    windows_langid = None
    if sys.platform == "win32":
        try:
            import ctypes

            windows_langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        except (AttributeError, OSError):  # pragma: no cover — зависит от системы
            windows_langid = None
    try:
        fallback = locale.getlocale()[0]
    except ValueError:  # pragma: no cover — испорченная локаль
        fallback = None

    return pick_language(
        override=override or os.environ.get(ENV_LANG),
        platform=sys.platform,
        windows_langid=windows_langid,
        env=os.environ,
        fallback=fallback,
    )


# --- тексты -----------------------------------------------------------------

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "2GIS Token",
        "heading": "Access token for 2GIS",
        "status_no_session": (
            "You have not signed in yet. Click “Sign in” — a browser window will open."
        ),
        "status_session": "You signed in earlier. Click “Get token” when you need one.",
        "status_signing_in": "A browser window is open. Sign in there; it will close by itself.",
        "status_getting": "Getting a token. This takes up to a minute.",
        "status_got": "The token is ready. Account: {account}.",
        "status_got_plain": "The token is ready.",
        "status_copied": "Copied. Paste it where you need it.",
        "status_installing": "Downloading the browser — about 150 MB, needed only once.",
        "status_installed": "The browser is installed. Now sign in.",
        "button_sign_in": "Sign in…",
        "button_get": "Get token",
        "button_copy": "Copy",
        "button_show": "Show",
        "button_hide": "Hide",
        "button_install": "Install browser",
        "token_empty": "no token yet",
        "note": "The token is not saved anywhere. Copy it before you close the window.",
        "error_page_not_loaded": (
            "2GIS did not load, so there was no token to catch. "
            "Check your connection and try again."
        ),
        "error_session_expired": "The session has expired. Sign in again.",
        "error_session_missing": "Sign in first.",
        "error_session_busy": (
            "Another run is using this session right now. Wait a moment and try again."
        ),
        "error_browser_missing": "The browser is not installed yet. Click “Install browser”.",
        "error_playwright_missing": "This copy has no Playwright. Install the program again.",
        "error_login_timeout": "Sign-in did not finish. Try again.",
        "error_install_failed": (
            "Could not download the browser (exit code {code}). Check your connection."
        ),
        "error_unexpected": "Something went wrong: {detail}",
    },
    "ru": {
        "title": "Токен 2ГИС",
        "heading": "Токен доступа к 2ГИС",
        "status_no_session": "Вход ещё не выполнен. Нажмите «Войти» — откроется окно браузера.",
        "status_session": "Вход уже выполнен. Нажмите «Получить токен», когда он понадобится.",
        "status_signing_in": "Открыто окно браузера. Войдите в аккаунт там — окно закроется само.",
        "status_getting": "Получаю токен. Это займёт до минуты.",
        "status_got": "Токен готов. Аккаунт: {account}.",
        "status_got_plain": "Токен готов.",
        "status_copied": "Скопировано. Вставьте туда, где он нужен.",
        "status_installing": "Скачиваю браузер — около 150 МБ, это нужно один раз.",
        "status_installed": "Браузер установлен. Теперь войдите.",
        "button_sign_in": "Войти…",
        "button_get": "Получить токен",
        "button_copy": "Копировать",
        "button_show": "Показать",
        "button_hide": "Скрыть",
        "button_install": "Установить браузер",
        "token_empty": "токена пока нет",
        "note": "Токен нигде не сохраняется. Скопируйте его, прежде чем закрыть окно.",
        "error_page_not_loaded": (
            "2ГИС не загрузился, и поймать токен не удалось. "
            "Проверьте связь и попробуйте ещё раз."
        ),
        "error_session_expired": "Сессия истекла. Войдите заново.",
        "error_session_missing": "Сначала войдите.",
        "error_session_busy": (
            "С этой сессией сейчас работает другой запуск. Подождите немного и попробуйте снова."
        ),
        "error_browser_missing": "Браузер ещё не установлен. Нажмите «Установить браузер».",
        "error_playwright_missing": "В этой копии нет Playwright. Установите программу заново.",
        "error_login_timeout": "Вход не завершился. Попробуйте ещё раз.",
        "error_install_failed": "Не удалось скачать браузер (код {code}). Проверьте связь.",
        "error_unexpected": "Что-то пошло не так: {detail}",
    },
}

#: Какой текст показать для какой ошибки.
#:
#: Порядок важен: ``PageNotLoaded`` — частный случай ``SessionExpired``, и
#: стоит первым, иначе незагрузившаяся страница звала бы человека входить
#: заново.
ERROR_TEXTS = (
    (PageNotLoaded, "error_page_not_loaded"),
    (SessionExpired, "error_session_expired"),
    (SessionMissing, "error_session_missing"),
    (SessionBusy, "error_session_busy"),
    (BrowserMissing, "error_browser_missing"),
    (PlaywrightMissing, "error_playwright_missing"),
    (TokenNotFound, "error_login_timeout"),
)


def describe_error(error: BaseException, language: str) -> str:
    """Что сказать человеку в окне.

    Консольные тексты ошибок сюда не годятся: они советуют команды вроде
    ``2gis-token login``, а у человека в окне есть кнопка «Войти». Поэтому
    текст выбирается по типу ошибки. Для непредвиденной ошибки показывается её
    собственный текст — прогнанный через ``redact``, чтобы токен не оказался
    на экране даже случайно.
    """
    texts = TEXTS[language]
    for kind, key in ERROR_TEXTS:
        if isinstance(error, kind):
            return texts[key]
    detail = redact(str(error)) or type(error).__name__
    return texts["error_unexpected"].format(detail=detail)
