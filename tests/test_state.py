"""Где лежит сессия, как она пишется и почему её нельзя трогать вдвоём."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from twogis_token.errors import SessionBusy, TokenExtractorError
from twogis_token.state import (
    APP_NAME,
    ENV_PROFILE,
    ENV_STATE,
    PROFILES_DIRNAME,
    STATE_FILENAME,
    SessionLock,
    commit_state,
    default_state_dir,
    default_state_path,
    known_profiles,
    lock_is_stale,
    lock_path,
    normalize_profile,
    profile_state_path,
    resolve_state_path,
    temp_state_path,
)

HOME = Path("/дом/пользователь")


class TestDefaultDir:
    def test_windows_берёт_appdata(self):
        result = default_state_dir("Windows", {"APPDATA": "/roaming"}, HOME)
        assert result == Path("/roaming") / APP_NAME

    def test_windows_без_appdata(self):
        result = default_state_dir("Windows", {}, HOME)
        assert result == HOME / "AppData" / "Roaming" / APP_NAME

    def test_macos(self):
        result = default_state_dir("Darwin", {}, HOME)
        assert result == HOME / "Library" / "Application Support" / APP_NAME

    def test_linux_по_умолчанию(self):
        assert default_state_dir("Linux", {}, HOME) == HOME / ".config" / APP_NAME

    def test_linux_уважает_xdg(self):
        result = default_state_dir("Linux", {"XDG_CONFIG_HOME": "/настройки"}, HOME)
        assert result == Path("/настройки") / APP_NAME

    def test_xdg_не_действует_на_windows(self):
        """Иначе переменная, оставшаяся от WSL или git-bash, увела бы файл не туда."""
        result = default_state_dir("Windows", {"XDG_CONFIG_HOME": "/настройки"}, HOME)
        assert result == HOME / "AppData" / "Roaming" / APP_NAME


def test_имя_файла():
    assert default_state_path("Linux", {}, HOME).name == STATE_FILENAME


class TestИмяПрофиля:
    def test_обычное_имя(self):
        assert normalize_profile("работа") == "работа"

    def test_пробелы_по_краям_убираются(self):
        assert normalize_profile("  работа  ") == "работа"

    @pytest.mark.parametrize(
        "name",
        ["", "   ", ".", "..", "../../etc", "рабочий/профиль", "C:\\профиль", "а*б", "а|б"],
    )
    def test_негодные_имена(self, name):
        """Имя становится каталогом, поэтому побег из каталога настроек закрыт."""
        with pytest.raises(TokenExtractorError):
            normalize_profile(name)


class TestПутьПрофиля:
    def test_складывается_из_каталога_настроек(self):
        result = profile_state_path("работа", "Linux", {}, HOME)
        assert result == HOME / ".config" / APP_NAME / PROFILES_DIRNAME / "работа" / STATE_FILENAME

    def test_разные_профили_не_пересекаются(self):
        первый = profile_state_path("личный", "Linux", {}, HOME)
        второй = profile_state_path("работа", "Linux", {}, HOME)
        assert первый != второй

    def test_профиль_не_совпадает_с_умолчанием(self):
        профиль = profile_state_path("личный", "Linux", {}, HOME)
        assert профиль != default_state_path("Linux", {}, HOME)


class TestResolve:
    def test_ключ_state_важнее_всего(self):
        result = resolve_state_path(
            "/явно/state.json",
            "работа",
            system="Linux",
            env={ENV_STATE: "/из/окружения.json", ENV_PROFILE: "другой"},
            home=HOME,
        )
        assert result == Path("/явно/state.json")

    def test_профиль_важнее_окружения(self):
        """Явно сказанное в команде сильнее забытой в оболочке переменной."""
        result = resolve_state_path(
            None, "работа", system="Linux", env={ENV_STATE: "/из/окружения.json"}, home=HOME
        )
        assert result == profile_state_path("работа", "Linux", {}, HOME)

    def test_переменная_state(self):
        result = resolve_state_path(
            None, None, system="Linux", env={ENV_STATE: "/из/окружения.json"}, home=HOME
        )
        assert result == Path("/из/окружения.json")

    def test_переменная_state_важнее_переменной_профиля(self):
        result = resolve_state_path(
            None,
            None,
            system="Linux",
            env={ENV_STATE: "/из/окружения.json", ENV_PROFILE: "работа"},
            home=HOME,
        )
        assert result == Path("/из/окружения.json")

    def test_переменная_профиля(self):
        result = resolve_state_path(
            None, None, system="Linux", env={ENV_PROFILE: "работа"}, home=HOME
        )
        assert result == profile_state_path("работа", "Linux", {}, HOME)

    def test_умолчание(self):
        result = resolve_state_path(None, None, system="Linux", env={}, home=HOME)
        assert result == HOME / ".config" / APP_NAME / STATE_FILENAME

    def test_пустая_переменная_не_считается(self):
        result = resolve_state_path(None, None, system="Linux", env={ENV_STATE: ""}, home=HOME)
        assert result == HOME / ".config" / APP_NAME / STATE_FILENAME

    def test_тильда_раскрывается(self):
        result = resolve_state_path("~/state.json", None, system="Linux", env={}, home=HOME)
        assert "~" not in str(result)


class TestСписокПрофилей:
    def test_пусто_когда_каталога_нет(self, tmp_path):
        assert known_profiles("Linux", {}, tmp_path) == []

    def test_видит_только_те_у_кого_есть_сессия(self, tmp_path):
        root = tmp_path / ".config" / APP_NAME / PROFILES_DIRNAME
        for name in ("работа", "личный"):
            (root / name).mkdir(parents=True)
            (root / name / STATE_FILENAME).write_text("{}", encoding="utf-8")
        (root / "пустой").mkdir(parents=True)  # каталог есть, входа не было

        assert known_profiles("Linux", {}, tmp_path) == ["личный", "работа"]


class TestЗапись:
    def test_временный_файл_рядом_но_не_поверх(self):
        target = Path("/каталог/storage_state.json")
        temp = temp_state_path(target)
        assert temp != target
        assert temp.parent == target.parent

    def test_замена_целиком(self, tmp_path):
        target = tmp_path / "storage_state.json"
        target.write_text("старая сессия", encoding="utf-8")
        temp = temp_state_path(target)
        temp.write_text("новая сессия", encoding="utf-8")

        commit_state(temp, target)

        assert target.read_text(encoding="utf-8") == "новая сессия"
        assert not temp.exists()

    def test_замена_работает_и_когда_файла_ещё_нет(self, tmp_path):
        target = tmp_path / "storage_state.json"
        temp = temp_state_path(target)
        temp.write_text("первая сессия", encoding="utf-8")

        commit_state(temp, target)

        assert target.read_text(encoding="utf-8") == "первая сессия"


class TestЗамок:
    def test_протухание(self):
        assert not lock_is_stale(0.0)
        assert not lock_is_stale(60.0)
        assert lock_is_stale(10_000.0)

    def test_второй_заход_отказывают(self, tmp_path):
        state = tmp_path / "storage_state.json"
        with SessionLock(state):
            with pytest.raises(SessionBusy):
                SessionLock(state).acquire()

    def test_после_освобождения_можно(self, tmp_path):
        state = tmp_path / "storage_state.json"
        with SessionLock(state):
            pass
        with SessionLock(state):  # не должно бросить
            pass

    def test_замок_убирается_за_собой(self, tmp_path):
        state = tmp_path / "storage_state.json"
        with SessionLock(state):
            assert lock_path(state).exists()
        assert not lock_path(state).exists()

    def test_замок_снимается_и_после_ошибки(self, tmp_path):
        state = tmp_path / "storage_state.json"
        with pytest.raises(RuntimeError):
            with SessionLock(state):
                raise RuntimeError("сбой посреди захода")
        assert not lock_path(state).exists()

    def test_брошенный_замок_забирается(self, tmp_path):
        """Иначе убитый по Ctrl+C заход запирал бы сессию навсегда."""
        state = tmp_path / "storage_state.json"
        SessionLock(state).acquire()  # намеренно не отпускаем
        старое = time.time() - 10_000
        os.utime(lock_path(state), (старое, старое))

        with SessionLock(state):  # не должно бросить
            pass

    def test_свежий_чужой_замок_не_забирается(self, tmp_path):
        state = tmp_path / "storage_state.json"
        SessionLock(state).acquire()
        with pytest.raises(SessionBusy):
            SessionLock(state).acquire()

    def test_разные_сессии_не_мешают_друг_другу(self, tmp_path):
        """Профили работают порознь, и замок одного не запирает другой."""
        первый = tmp_path / "личный.json"
        второй = tmp_path / "работа.json"
        with SessionLock(первый), SessionLock(второй):
            pass
