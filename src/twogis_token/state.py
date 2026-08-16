"""Где лежит сохранённая сессия браузера.

``storage_state.json`` — это куки, то есть фактически доступ к аккаунту. Поэтому:

* по умолчанию файл лежит **не в текущем каталоге**, а в пользовательском
  каталоге настроек — чтобы его нельзя было случайно закоммитить;
* на unix-системах при записи ему выставляются права ``600``;
* путь можно переопределить ключом ``--state`` или переменной окружения.

Выбор каталога — чистая функция от системы и окружения, поэтому проверяется
тестами на всех трёх платформах сразу, без запуска на них.
"""

from __future__ import annotations

import os
import platform
import stat
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "2gis-token-extractor"
STATE_FILENAME = "storage_state.json"

#: Переменная окружения, задающая путь к файлу сессии.
ENV_STATE = "TWOGIS_TOKEN_STATE"


def default_state_dir(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Каталог настроек программы по правилам конкретной системы."""
    system = system if system is not None else platform.system()
    env = env if env is not None else os.environ
    home = home if home is not None else Path.home()

    if system == "Windows":
        base = env.get("APPDATA") or str(home / "AppData" / "Roaming")
    elif system == "Darwin":
        base = str(home / "Library" / "Application Support")
    else:
        base = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(base) / APP_NAME


def default_state_path(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return default_state_dir(system, env, home) / STATE_FILENAME


def resolve_state_path(
    explicit: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Путь к файлу сессии: ключ командной строки, потом переменная, потом умолчание."""
    if explicit:
        return Path(explicit).expanduser()

    env = env if env is not None else os.environ
    from_env = env.get(ENV_STATE)
    if from_env:
        return Path(from_env).expanduser()

    return default_state_path(system, env, home)


def harden(path: Path) -> None:
    """Оставляет доступ к файлу только владельцу.

    На Windows ничего не делает: там права устроены иначе, и подпирать их
    через ``chmod`` бессмысленно — каталог пользователя и так закрыт.
    """
    if os.name == "nt":
        return
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def prepare_parent(path: Path) -> None:
    """Создаёт каталог под файл сессии."""
    path.parent.mkdir(parents=True, exist_ok=True)
