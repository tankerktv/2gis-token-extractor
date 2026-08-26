"""Ошибки программы и коды возврата.

Код возврата — часть интерфейса: программу запускают из скриптов, и там важно
отличать «войди заново» от «нет браузера» и от «лежит сеть». Поэтому код
приписан к типу ошибки, а не выбирается в месте печати.

    0 — успех
    1 — токен не найден или не принят сервером
    2 — нужна авторизация: сессии нет или она истекла
    3 — окружение не готово: нет Playwright или браузера
    4 — сеть недоступна
    5 — сессия занята другим заходом
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_TOKEN = 1
EXIT_AUTH = 2
EXIT_ENVIRONMENT = 3
EXIT_NETWORK = 4
EXIT_BUSY = 5


class TokenExtractorError(RuntimeError):
    """Ошибка, о которой пользователю нужно сказать словами, а не трассировкой."""

    exit_code = EXIT_TOKEN


class TokenNotFound(TokenExtractorError):
    """Браузер отработал, а токена в его запросах не оказалось."""

    exit_code = EXIT_TOKEN


class SessionMissing(TokenExtractorError):
    """Сохранённой сессии нет — пользователь ещё ни разу не входил."""

    exit_code = EXIT_AUTH


class SessionExpired(TokenExtractorError):
    """Сессия есть, но 2ГИС её больше не признаёт."""

    exit_code = EXIT_AUTH


class PlaywrightMissing(TokenExtractorError):
    """Не установлен пакет playwright."""

    exit_code = EXIT_ENVIRONMENT


class BrowserMissing(TokenExtractorError):
    """Playwright есть, а браузера он себе ещё не скачал."""

    exit_code = EXIT_ENVIRONMENT


class SessionBusy(TokenExtractorError):
    """С этой сессией прямо сейчас работает другой заход.

    Два браузера на одном файле — это гонка: 2ГИС обновляет куки при визите,
    и тот, кто сохранится вторым, затрёт чужие свежие куки своими устаревшими.
    Потерянная сессия стоит человеку новой SMS, поэтому лучше отказать.
    """

    exit_code = EXIT_BUSY


class NetworkUnavailable(TokenExtractorError):
    """До 2ГИС не достучаться — это не то же самое, что протухший токен."""

    exit_code = EXIT_NETWORK
