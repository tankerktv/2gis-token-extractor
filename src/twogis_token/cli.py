"""Команды программы: login, get, check.

Договорённости про вывод, ради которых всё и затевалось:

* ``get`` печатает в stdout **только токен и перевод строки** — чтобы его можно
  было подставить в конвейер:  ``export ZOND_TOKEN="$(2gis-token get)"``;
* всё остальное — подсказки, ход дела, ошибки — идёт в stderr и конвейеру
  не мешает;
* токен не пишется ни в файлы, ни в логи, пока об этом не попросили ключом
  ``--out``; в сообщениях вместо токена стоит его отпечаток.

Печать идёт через ``sys.stdout.buffer`` в UTF-8 явно. Обычный ``print`` берёт
кодировку из окружения, и на машине с кириллической консолью падает на первом
же длинном тире — на соседнем проекте это уронило выпуск релиза.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import __version__, auth_api, browser
from .errors import EXIT_AUTH, EXIT_NETWORK, EXIT_OK, EXIT_TOKEN, TokenExtractorError
from .state import ENV_STATE, harden, prepare_parent, resolve_state_path
from .tokens import fingerprint, is_token

PROGRAM = "2gis-token"


# --- вывод ------------------------------------------------------------------


def _write(stream, text: str) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is None:  # stdout подменён, например в тестах
        stream.write(text + "\n")
        return
    buffer.write(text.encode("utf-8") + b"\n")
    stream.flush()


def out(text: str) -> None:
    _write(sys.stdout, text)


def err(text: str) -> None:
    _write(sys.stderr, text)


# --- разбор аргументов ------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state",
        metavar="ФАЙЛ",
        help=f"файл сессии браузера (по умолчанию — каталог настроек, ${ENV_STATE})",
    )
    parser.add_argument(
        "--url",
        default=browser.DEFAULT_URL,
        help="страница, на которой ловится токен (по умолчанию %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="подробности машиночитаемо")
    parser.add_argument("-v", "--verbose", action="store_true", help="ход дела в stderr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Выдаёт токен доступа к 2ГИС.",
        epilog="Токен печатается в stdout. Что делать с ним дальше — решает вызывающий.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="КОМАНДА")

    login = commands.add_parser(
        "login",
        help="открыть браузер и войти в аккаунт (один раз)",
        description="Открывает окно браузера. Телефон и код из SMS вводишь ты, "
        "программа их не видит — на диск ложатся только куки.",
    )
    _add_common(login)
    login.add_argument(
        "--timeout",
        type=float,
        default=browser.LOGIN_TIMEOUT,
        metavar="СЕК",
        help="сколько ждать входа (по умолчанию %(default)s)",
    )

    get = commands.add_parser(
        "get",
        help="напечатать свежий токен",
        description="Заходит на 2ГИС с сохранённой сессией и печатает токен в stdout.",
    )
    _add_common(get)
    get.add_argument(
        "--timeout",
        type=float,
        default=browser.DEFAULT_TIMEOUT,
        metavar="СЕК",
        help="сколько ждать токен (по умолчанию %(default)s)",
    )
    get.add_argument(
        "--out",
        metavar="ФАЙЛ",
        help="записать токен в файл вместо печати в stdout",
    )
    get.add_argument(
        "--headed",
        action="store_true",
        help="показать окно браузера (для разбирательств)",
    )

    check = commands.add_parser(
        "check",
        help="жив ли токен",
        description="Спрашивает у api.auth.2gis.com, действует ли токен. "
        "Без --token берёт свежий токен из сохранённой сессии.",
    )
    _add_common(check)
    check.add_argument(
        "--timeout",
        type=float,
        default=browser.DEFAULT_TIMEOUT,
        metavar="СЕК",
        help="сколько ждать токен из браузера (по умолчанию %(default)s)",
    )
    check.add_argument(
        "--token",
        metavar="ТОКЕН",
        help="проверить этот токен; '-' — прочитать из stdin",
    )

    commands.add_parser(
        "install-browser",
        help="скачать браузер для Playwright",
        description="Скачивает Chromium. Нужно один раз, и только если браузера ещё нет.",
    )

    return parser


# --- команды ----------------------------------------------------------------


def _state_path(args) -> Path:
    return resolve_state_path(getattr(args, "state", None))


def _acquire(args) -> browser.Capture:
    """Достаёт токен из браузера — общая часть ``get`` и ``check``."""
    return asyncio.run(
        browser.capture_token(
            _state_path(args),
            url=args.url,
            timeout=args.timeout,
            headless=not getattr(args, "headed", False),
        )
    )


def cmd_login(args) -> int:
    state = _state_path(args)
    capture = asyncio.run(
        browser.interactive_login(state, url=args.url, timeout=args.timeout, on_message=err)
    )
    if args.json:
        out(json.dumps({"state": str(state), "fingerprint": fingerprint(capture.token)}))
    else:
        err(f"Готово: {fingerprint(capture.token)}")
        err(f"Дальше:  {PROGRAM} get")
    return EXIT_OK


def cmd_get(args) -> int:
    capture = _acquire(args)
    state = _state_path(args)

    if args.out:
        path = Path(args.out).expanduser()
        prepare_parent(path)
        path.write_text(capture.token + "\n", encoding="utf-8")
        harden(path)
        err(f"Токен записан в {path}")
        if args.json:
            out(
                json.dumps(
                    {
                        "out": str(path),
                        "source": capture.source,
                        "state": str(state),
                        "fingerprint": fingerprint(capture.token),
                    }
                )
            )
        return EXIT_OK

    if args.json:
        out(
            json.dumps(
                {
                    "token": capture.token,
                    "source": capture.source,
                    "state": str(state),
                }
            )
        )
    else:
        out(capture.token)
    return EXIT_OK


def _token_argument(args) -> str | None:
    """Токен, переданный руками: значением или через stdin."""
    if not args.token:
        return None
    if args.token == "-":
        return sys.stdin.read().strip()
    return args.token.strip()


def _check_exit_code(result: auth_api.CheckResult) -> int:
    if result.alive:
        return EXIT_OK
    if not result.reachable:
        return EXIT_NETWORK
    if result.expired:
        return EXIT_AUTH
    return EXIT_TOKEN


def cmd_check(args) -> int:
    token = _token_argument(args)
    source = "аргумент"
    if token is None:
        capture = _acquire(args)
        token, source = capture.token, capture.source
    elif not is_token(token):
        raise TokenExtractorError(
            "это не похоже на токен 2ГИС: ожидается 40 знаков 0-9 и a-f"
        )

    result = auth_api.check_token(token)

    if args.json:
        out(
            json.dumps(
                {
                    "alive": result.alive,
                    "expired": result.expired,
                    "reachable": result.reachable,
                    "status": result.status,
                    "detail": result.detail,
                    "account": result.account,
                    "source": source,
                    "fingerprint": fingerprint(token),
                },
                ensure_ascii=False,
            )
        )
    else:
        out(auth_api.describe(token, result))
    return _check_exit_code(result)


def cmd_install_browser(args) -> int:
    return browser.install_browser()


COMMANDS = {
    "login": cmd_login,
    "get": cmd_get,
    "check": cmd_check,
    "install-browser": cmd_install_browser,
}


# --- точка входа ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        stream=sys.stderr,
        format="%(message)s",
    )

    try:
        return COMMANDS[args.command](args)
    except TokenExtractorError as error:
        err(f"{PROGRAM}: {error}")
        return error.exit_code
    except KeyboardInterrupt:  # pragma: no cover — интерактивное прерывание
        err("прервано")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
