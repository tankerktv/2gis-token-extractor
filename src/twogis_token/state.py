"""Где лежит сохранённая сессия браузера и как её не потерять.

``storage_state.json`` — это куки, то есть фактически доступ к аккаунту. Поэтому:

* по умолчанию файл лежит **не в текущем каталоге**, а в пользовательском
  каталоге настроек — чтобы его нельзя было случайно закоммитить;
* на unix-системах при записи ему выставляются права ``600``;
* путь можно переопределить ключом ``--state``, именем профиля или
  переменной окружения.

**Профили** нужны тем, у кого больше одного аккаунта: личный и рабочий — это
две разные сессии, и держать их надо порознь. ``--profile работа`` раскладывает
их по каталогу настроек сам, чтобы не запоминать пути.

Выбор каталога — чистая функция от системы и окружения, поэтому проверяется
тестами на всех трёх платформах сразу, без запуска на них.

**Про запись.** Сессия пишется через временный файл и ``os.replace``: прямая
запись поверх боевого файла означает, что сбой посередине оставит обрубок, а
обрубок — это потерянный вход и новая SMS. Плюс замок: два браузера на одной
сессии — гонка, в которой проигравший затирает свежие чужие куки своими
устаревшими.
"""

from __future__ import annotations

import os
import platform
import stat
import time
from collections.abc import Mapping
from pathlib import Path

from .errors import SessionBusy, TokenExtractorError

APP_NAME = "2gis-token-extractor"
STATE_FILENAME = "storage_state.json"
PROFILES_DIRNAME = "profiles"

#: Переменные окружения — те же настройки для тех, кому неудобны ключи.
ENV_STATE = "TWOGIS_TOKEN_STATE"
ENV_PROFILE = "TWOGIS_TOKEN_PROFILE"

#: Знаки, которых не может быть в имени профиля: оно становится именем каталога.
#: Дело не только в Windows — без этой проверки ``--profile ../../etc`` увёл бы
#: запись куда угодно.
FORBIDDEN_IN_PROFILE = set('/\\:*?"<>|')

#: Через сколько замок считается брошенным. Заведомо больше самого долгого
#: захода — ``login`` ждёт человека до десяти минут.
STALE_LOCK_AFTER = 900.0

LOCK_SUFFIX = ".lock"
TEMP_SUFFIX = ".new"


# --- где лежит сессия -------------------------------------------------------


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


def normalize_profile(name: str) -> str:
    """Имя профиля, пригодное для имени каталога.

    Проверка строгая нарочно: имя приходит из командной строки и становится
    частью пути, а ``..`` в пути — это запись мимо каталога настроек.
    """
    cleaned = (name or "").strip()
    if not cleaned or cleaned in {".", ".."} or set(cleaned) & FORBIDDEN_IN_PROFILE:
        raise TokenExtractorError(
            f"негодное имя профиля: {name!r}\n"
            "Имя становится именем каталога — нельзя пустое, '.', '..' "
            "и знаки / \\ : * ? \" < > |"
        )
    return cleaned


def profile_state_path(
    name: str,
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    root = default_state_dir(system, env, home) / PROFILES_DIRNAME
    return root / normalize_profile(name) / STATE_FILENAME


def resolve_state_path(
    explicit: str | os.PathLike[str] | None = None,
    profile: str | None = None,
    *,
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Путь к файлу сессии.

    Порядок: ключи командной строки, потом окружение, потом умолчание. Явно
    сказанное в команде всегда сильнее переменной — иначе забытая в профиле
    оболочки переменная молча уводила бы работу не в тот аккаунт.
    """
    if explicit:
        return Path(explicit).expanduser()
    if profile:
        return profile_state_path(profile, system, env, home)

    env = env if env is not None else os.environ
    from_env = env.get(ENV_STATE)
    if from_env:
        return Path(from_env).expanduser()
    profile_from_env = env.get(ENV_PROFILE)
    if profile_from_env:
        return profile_state_path(profile_from_env, system, env, home)

    return default_state_path(system, env, home)


def known_profiles(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[str]:
    """Профили, у которых на диске есть сохранённая сессия."""
    root = default_state_dir(system, env, home) / PROFILES_DIRNAME
    if not root.is_dir():
        return []
    return sorted(
        item.name for item in root.iterdir() if (item / STATE_FILENAME).exists()
    )


# --- как она пишется --------------------------------------------------------


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


def temp_state_path(state_path: Path) -> Path:
    """Куда писать до того, как замена станет окончательной."""
    return state_path.with_name(state_path.name + TEMP_SUFFIX)


def commit_state(temp: Path, target: Path) -> None:
    """Ставит записанный файл на место одним движением.

    ``os.replace`` заменяет целиком: либо старая сессия, либо новая, но
    никогда не половина новой. Права выставляются до замены, чтобы файл не
    полежал открытым даже мгновение.
    """
    harden(temp)
    os.replace(temp, target)


# --- замок ------------------------------------------------------------------


def lock_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + LOCK_SUFFIX)


def lock_is_stale(age: float, stale_after: float = STALE_LOCK_AFTER) -> bool:
    """Замок брошен, если он старше самого долгого разумного захода.

    Без этого правила убитый по Ctrl+C заход оставлял бы сессию запертой
    навсегда, и человеку пришлось бы удалять файл руками.
    """
    return age >= stale_after


class SessionLock:
    """Не даёт двум заходам работать с одной сессией одновременно.

    Замок — обычный файл рядом с сессией, создаваемый с ``O_EXCL``: это
    единственная проверка-и-создание, атомарная на всех трёх системах и не
    требующая зависимостей.
    """

    def __init__(self, state_path: Path, stale_after: float = STALE_LOCK_AFTER) -> None:
        self._path = lock_path(state_path)
        self._stale_after = stale_after
        self._held = False

    def acquire(self) -> None:
        prepare_parent(self._path)
        for attempt in (1, 2):
            try:
                handle = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 2 or not self._steal_if_stale():
                    raise SessionBusy(
                        f"с этой сессией уже работает другой заход ({self._path}).\n"
                        "Подожди, пока он закончится. Если процесс давно умер,\n"
                        "удали файл замка."
                    ) from None
                continue
            os.write(handle, str(os.getpid()).encode("ascii"))
            os.close(handle)
            self._held = True
            return

    def _steal_if_stale(self) -> bool:
        try:
            age = time.time() - self._path.stat().st_mtime
        except OSError:  # замок исчез между попытками — тем лучше
            return True
        if not lock_is_stale(age, self._stale_after):
            return False
        try:
            self._path.unlink()
        except OSError:
            return False
        return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._path.unlink()
        except OSError:
            pass
        self._held = False

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

    # Асинхронная пара нужна, чтобы замок вставал в один ``async with`` рядом
    # с Playwright. Ждать тут нечего, работа мгновенная — оттого и такая
    # короткая обёртка.
    async def __aenter__(self) -> SessionLock:
        self.acquire()
        return self

    async def __aexit__(self, *exc_info) -> None:
        self.release()
