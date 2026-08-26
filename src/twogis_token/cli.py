"""Команды программы: login, get, check, profiles.

Договорённости про вывод, ради которых всё и затевалось:

* ``get`` печатает в stdout **только токен и перевод строки** — чтобы его можно
  было подставить в конвейер:  ``export ZOND_TOKEN="$(2gis-token get)"``;
* всё остальное — подсказки, ход дела, ошибки — идёт в stderr и конвейеру
  не мешает;
* токен не пишется ни в файлы, ни в логи, пока об этом не попросили ключом
  ``--out``; в сообщениях вместо токена стоит его отпечаток.

Справка (``--help``) — на английском: README у проекта тоже английский,
а команда попадает на глаза первой.

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
from .state import (
    ENV_PROFILE,
    ENV_STATE,
    harden,
    known_profiles,
    prepare_parent,
    resolve_state_path,
)
from .tokens import fingerprint, is_token

PROGRAM = "2gis-token"

EPILOG = """\
examples:
  2gis-token login                    sign in once, in a real browser window
  2gis-token get                      print a fresh token
  2gis-token check                    ask 2GIS whether the token still works
  2gis-token get --out token.txt      write it to a file instead of stdout
  2gis-token login --profile work     keep a second account separate

  export ZOND_TOKEN="$(2gis-token get)"

exit codes:
  0  success
  1  token not found, or not accepted by 2GIS
  2  sign-in required: no session, or it expired
  3  environment not ready: no Playwright, or no browser for it
  4  network unavailable
  5  session busy: another run is using it right now

The token goes to stdout and nowhere else. It reaches a file only when you ask
for one with --out, and it never appears in logs: messages carry a fingerprint
instead. Your phone number and the SMS code are typed into a real browser and
never pass through this program.
"""


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


def _add_session(parser: argparse.ArgumentParser) -> None:
    """Ключи, выбирающие сессию. Общие для всех команд, которые её трогают."""
    parser.add_argument(
        "--state",
        metavar="FILE",
        help=f"session file to use (default: config directory, ${ENV_STATE})",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help=f"named session, kept apart from the others (${ENV_PROFILE})",
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    _add_session(parser)
    parser.add_argument(
        "--url",
        default=browser.DEFAULT_URL,
        help="page to catch the token on (default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable details")
    parser.add_argument("-v", "--verbose", action="store_true", help="progress to stderr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Print a fresh 2GIS access token to stdout.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    login = commands.add_parser(
        "login",
        help="sign in once, in a real browser window",
        description=(
            "Opens a browser window on 2gis.ru. You type the phone number and the "
            "code from the SMS yourself; the program never sees either. Only the "
            "browser cookies are stored, in the config directory. The window "
            "closes by itself once the session is ready."
        ),
    )
    _add_common(login)
    login.add_argument(
        "--timeout",
        type=float,
        default=browser.LOGIN_TIMEOUT,
        metavar="SEC",
        help="how long to wait for you to sign in (default: %(default)s)",
    )

    get = commands.add_parser(
        "get",
        help="print a fresh token",
        description=(
            "Loads 2GIS in a headless browser with the saved session and prints "
            "the token: one line, nothing else, so it drops straight into a "
            "pipeline. Every run yields a new token; the old ones keep working."
        ),
    )
    _add_common(get)
    get.add_argument(
        "--timeout",
        type=float,
        default=browser.DEFAULT_TIMEOUT,
        metavar="SEC",
        help="how long to wait for the token (default: %(default)s)",
    )
    get.add_argument(
        "--out",
        metavar="FILE",
        help="write the token to this file; stdout stays empty",
    )
    get.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window, for troubleshooting",
    )

    check = commands.add_parser(
        "check",
        help="check whether a token is still alive",
        description=(
            "Asks api.auth.2gis.com for the account profile. Without --token it "
            "takes a fresh token from the saved session, which answers a "
            "different question: whether the session still works."
        ),
    )
    _add_common(check)
    check.add_argument(
        "--timeout",
        type=float,
        default=browser.DEFAULT_TIMEOUT,
        metavar="SEC",
        help="how long to wait for a token from the browser (default: %(default)s)",
    )
    check.add_argument(
        "--token",
        metavar="TOKEN",
        help="check this token instead; '-' reads it from stdin",
    )

    profiles = commands.add_parser(
        "profiles",
        help="list saved profiles",
        description="Lists the named sessions that have been signed in.",
    )
    profiles.add_argument("--json", action="store_true", help="machine-readable details")

    commands.add_parser(
        "install-browser",
        help="download the browser Playwright needs",
        description=(
            "Downloads Chromium. Needed once, and only if Playwright has no "
            "browser yet — for instance when you took a single-file build."
        ),
    )

    return parser


# --- команды ----------------------------------------------------------------


def _state_path(args) -> Path:
    return resolve_state_path(getattr(args, "state", None), getattr(args, "profile", None))


def _acquire(args) -> browser.Capture:
    """Достаёт токен из браузера — общая часть ``get`` и ``check``."""
    return asyncio.run(
        browser.capture_token(
            _state_path(args),
            url=args.url,
            timeout=args.timeout,
            headless=not getattr(args, "headed", False),
            profile=getattr(args, "profile", None),
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
        err(f"Done: {fingerprint(capture.token)}")
        err(f"Next:  {PROGRAM} get" + (f" --profile {args.profile}" if args.profile else ""))
    return EXIT_OK


def cmd_get(args) -> int:
    capture = _acquire(args)
    state = _state_path(args)

    if args.out:
        path = Path(args.out).expanduser()
        prepare_parent(path)
        path.write_text(capture.token + "\n", encoding="utf-8")
        harden(path)
        err(f"Token written to {path}")
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
    source = "argument"
    if token is None:
        capture = _acquire(args)
        token, source = capture.token, capture.source
    elif not is_token(token):
        raise TokenExtractorError(
            "that does not look like a 2GIS token: expected 40 characters, 0-9 and a-f"
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


def cmd_profiles(args) -> int:
    names = known_profiles()
    if args.json:
        out(json.dumps(names, ensure_ascii=False))
        return EXIT_OK
    if not names:
        err("no saved profiles")
        err(f"create one:  {PROGRAM} login --profile NAME")
        return EXIT_OK
    for name in names:
        out(name)
    return EXIT_OK


def cmd_install_browser(args) -> int:
    return browser.install_browser()


COMMANDS = {
    "login": cmd_login,
    "get": cmd_get,
    "check": cmd_check,
    "profiles": cmd_profiles,
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
        err("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
