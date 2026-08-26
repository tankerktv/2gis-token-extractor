"""Что такое токен 2ГИС и как узнать его в строке.

Формат измерен, а не предположен: **40 символов, только 0-9 и a-f**. Это
непрозрачная строка, а не JWT — точек нет, base64 нет, разобрать на части
нельзя, срок годности внутри не написан.

Отсюда осторожность с поиском. Сорок шестнадцатеричных знаков — это ещё и
любой sha1, а их на странице карты полно: отпечатки картинок, версии сборки,
идентификаторы тайлов. Поэтому поиск устроен от надёжного к сомнительному:

    websocket → адрес сокета zond, где токен лежит в параметре ``token``
    query     → тот же параметр в обычном запросе
    header    → заголовок вроде ``X-Token`` или ``Authorization``
    text      → просто сорок знаков подряд в тексте

``TokenCollector`` собирает найденное и отдаёт лучшее, а не первое попавшееся:
если сомнительный кандидат пришёл раньше надёжного, побеждает надёжный.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

#: Длина токена в символах.
TOKEN_LENGTH = 40

#: Токен целиком и ничего кроме него.
TOKEN_EXACT = re.compile(r"[0-9a-f]{40}")

#: Токен внутри строки. Границы слова обязательны: без них шаблон выкусил бы
#: первые сорок знаков из более длинной шестнадцатеричной строки и выдал бы
#: обрубок за токен.
TOKEN_IN_TEXT = re.compile(r"\b[0-9a-f]{40}\b")

#: Параметры запроса, в которых 2ГИС носит токен.
TOKEN_PARAMS = ("token", "access_token")

#: Заголовки, в которых токен встречается. Имена сравниваются без учёта регистра.
TOKEN_HEADERS = ("x-token", "token", "access-token", "x-access-token", "authorization")

#: Чем надёжнее источник, тем больше вес. Сравниваются только веса, конкретные
#: числа значения не имеют — важен порядок.
SOURCE_WEIGHT = {
    "websocket": 40,
    "query": 30,
    "header": 20,
    "text": 10,
}


def is_token(value: object) -> bool:
    """Похожа ли строка на токен 2ГИС.

    Заглавные буквы не принимаются намеренно: 2ГИС выдаёт токен в нижнем
    регистре, а послабление здесь означало бы, что под определение попадёт
    вдвое больше посторонних шестнадцатеричных строк.
    """
    return isinstance(value, str) and TOKEN_EXACT.fullmatch(value) is not None


def find_token(text: object) -> str | None:
    """Первый токен в произвольном тексте. Самый ненадёжный способ из всех."""
    if not isinstance(text, str):
        return None
    match = TOKEN_IN_TEXT.search(text)
    return match.group(0) if match else None


def token_from_url(url: object) -> str | None:
    """Токен из параметров запроса — ``?token=`` или ``?access_token=``.

    Именно так он приезжает в адресе сокета:

        wss://zond.api.2gis.ru/api/1.1/user/ws?appVersion=6.31.0&channels=...&token=<40 hex>
    """
    if not isinstance(url, str) or "?" not in url:
        return None
    query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    for name, value in query:
        if name.lower() in TOKEN_PARAMS and is_token(value):
            return value
    return None


def token_from_headers(headers: Mapping[str, str] | None) -> str | None:
    """Токен из заголовков запроса.

    ``Authorization: Bearer <токен>`` разбирается тоже: схема отбрасывается,
    проверяется остаток.
    """
    if not headers:
        return None
    for name, value in headers.items():
        if not isinstance(value, str) or name.lower() not in TOKEN_HEADERS:
            continue
        candidate = value.strip()
        if " " in candidate:  # "Bearer <токен>", "OAuth <токен>"
            candidate = candidate.rsplit(" ", 1)[1]
        if is_token(candidate):
            return candidate
    return None


def fingerprint(token: str | None) -> str:
    """Описание токена, безопасное для логов и экрана.

    Токен — это доступ к аккаунту, поэтому в сообщениях он не появляется
    никогда. Отпечатка хватает, чтобы понять, тот же это токен или новый.
    """
    if not token:
        return "<empty>"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:8]
    kind = "token" if is_token(token) else f"string len={len(token)}"
    return f"<{kind} sha1={digest}>"


def redact(text: str) -> str:
    """Заменяет все токены в тексте заглушкой — для логов и сообщений об ошибках."""
    return TOKEN_IN_TEXT.sub("<TOKEN>", text)


@dataclass(frozen=True)
class Found:
    """Найденный токен и то, откуда он взялся."""

    token: str
    source: str

    @property
    def weight(self) -> int:
        return SOURCE_WEIGHT[self.source]


class TokenCollector:
    """Копит кандидатов и отдаёт самого надёжного.

    Браузер сыплет событиями в произвольном порядке, и первое совпадение
    запросто окажется чужим sha1 из адреса картинки. Поэтому кандидат с более
    надёжным источником вытесняет уже найденного, а равный по надёжности —
    нет: при прочих равных доверяем тому, что пришло раньше.
    """

    def __init__(self) -> None:
        self._best: Found | None = None

    def offer(self, token: str | None, source: str) -> None:
        if not is_token(token) or source not in SOURCE_WEIGHT:
            return
        found = Found(str(token), source)
        if self._best is None or found.weight > self._best.weight:
            self._best = found

    def offer_websocket_url(self, url: str | None) -> None:
        """Адрес веб-сокета — самый надёжный источник, других сокетов на странице нет."""
        self.offer(token_from_url(url) or find_token(url), "websocket")

    def offer_request_url(self, url: str | None) -> None:
        self.offer(token_from_url(url), "query")

    def offer_headers(self, headers: Mapping[str, str] | None) -> None:
        self.offer(token_from_headers(headers), "header")

    def offer_text(self, text: str | None) -> None:
        self.offer(find_token(text), "text")

    def offer_all(self, values: Iterable[str]) -> None:
        for value in values:
            self.offer_text(value)

    @property
    def found(self) -> Found | None:
        return self._best

    @property
    def token(self) -> str | None:
        return self._best.token if self._best else None

    @property
    def source(self) -> str | None:
        return self._best.source if self._best else None

    def __bool__(self) -> bool:
        return self._best is not None
