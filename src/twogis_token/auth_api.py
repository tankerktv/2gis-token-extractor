"""Проверка токена через профиль пользователя.

    GET https://api.auth.2gis.com/2.1/users/me?access_token=<токен>

Живой токен отдаёт профиль, протухший — 401 или 403. Это единственный запрос,
который программа делает сама, и стоит он дёшево.

Разбор ответа отделён от самого запроса нарочно: ``parse_users_me`` — чистая
функция, её можно прогнать по всем интересным ответам, не выходя в сеть.

Отдельно различаются три исхода, которые легко слить в один и потом об этом
пожалеть: **токен протух** (нужно входить заново), **сервис ответил странно**
(ждать и не трогать сессию), **сети нет** (проблема на нашей стороне).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from .tokens import fingerprint

USERS_ME_URL = "https://api.auth.2gis.com/2.1/users/me"

#: Ключи, из которых складывается человеческое имя аккаунта. Почта и телефон
#: сюда не входят намеренно: вывод команды попадает в логи и на экраны,
#: а имени достаточно, чтобы убедиться, что аккаунт тот самый.
NAME_KEYS = ("display_name", "name", "first_name", "nickname", "id")

#: Обёртки, в которые сервисы любят класть полезную часть ответа.
ENVELOPE_KEYS = ("result", "data", "user", "profile")

DEFAULT_TIMEOUT = 10.0

Fetch = Callable[[str, float], "tuple[int, str]"]


@dataclass(frozen=True)
class CheckResult:
    """Итог проверки токена."""

    alive: bool
    detail: str
    status: int | None = None
    account: str | None = None
    expired: bool = False
    reachable: bool = True

    def __bool__(self) -> bool:
        return self.alive


def account_name(payload: object) -> str | None:
    """Достаёт имя аккаунта из ответа, не полагаясь на его форму.

    Ответ может прийти как плоским объектом, так и завёрнутым в ``result``.
    Гадать не нужно — проверяются оба варианта, а если имени нет вовсе,
    возвращается ``None``, и вызывающий скажет просто «жив».
    """
    if not isinstance(payload, dict):
        return None
    for key in NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    for key in ENVELOPE_KEYS:
        nested = account_name(payload.get(key))
        if nested:
            return nested
    return None


def parse_users_me(status: int, body: str) -> CheckResult:
    """Превращает ответ ``users/me`` в понятный итог."""
    if status == 200:
        try:
            payload = json.loads(body)
        except ValueError:
            return CheckResult(
                alive=False,
                detail="HTTP 200, but the body is not JSON — 2GIS changed something",
                status=status,
            )
        name = account_name(payload)
        return CheckResult(
            alive=True,
            detail=f"alive, account {name}" if name else "alive",
            status=status,
            account=name,
        )

    if status in (401, 403):
        return CheckResult(
            alive=False,
            detail=(
                f"token rejected (HTTP {status}) — the session has expired, "
                "sign in again: 2gis-token login"
            ),
            status=status,
            expired=True,
        )

    if 500 <= status < 600:
        return CheckResult(
            alive=False,
            detail=f"2GIS is failing (HTTP {status}) — their side; the token is not the problem",
            status=status,
        )

    return CheckResult(
        alive=False,
        detail=f"unexpected response, HTTP {status}",
        status=status,
    )


def _urlopen_fetch(url: str, timeout: float) -> tuple[int, str]:
    """Запрос через стандартную библиотеку — чтобы не тащить httpx ради одного GET."""
    request = urllib.request.Request(url, headers={"User-Agent": "2gis-token-extractor"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:  # 4xx/5xx — это ответ, а не сбой
        return error.code, error.read().decode("utf-8", "replace")


def check_token(
    token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    fetch: Fetch | None = None,
) -> CheckResult:
    """Спрашивает у 2ГИС, действует ли токен.

    ``fetch`` подменяется в тестах: сеть в них не нужна, а все интересные
    ответы всё равно разбирает ``parse_users_me``.
    """
    if not token:
        return CheckResult(alive=False, detail="the token is empty")

    url = f"{USERS_ME_URL}?{urllib.parse.urlencode({'access_token': token})}"
    call = fetch or _urlopen_fetch
    try:
        status, body = call(url, timeout)
    except urllib.error.URLError as error:
        return CheckResult(
            alive=False,
            detail=f"network unavailable: {error.reason}",
            reachable=False,
        )
    except OSError as error:
        return CheckResult(
            alive=False,
            detail=f"network unavailable: {error}",
            reachable=False,
        )
    return parse_users_me(status, body)


def describe(token: str, result: CheckResult) -> str:
    """Строка для человека: отпечаток токена и итог. Самого токена в ней нет."""
    return f"{fingerprint(token)} {result.detail}"
