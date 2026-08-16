"""Выбор пути к файлу сессии — на всех трёх системах сразу."""

from __future__ import annotations

from pathlib import Path

from twogis_token.state import (
    APP_NAME,
    ENV_STATE,
    STATE_FILENAME,
    default_state_dir,
    default_state_path,
    resolve_state_path,
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


class TestResolve:
    def test_ключ_важнее_всего(self):
        result = resolve_state_path(
            "/явно/state.json",
            system="Linux",
            env={ENV_STATE: "/из/окружения.json"},
            home=HOME,
        )
        assert result == Path("/явно/state.json")

    def test_переменная_окружения(self):
        result = resolve_state_path(
            None, system="Linux", env={ENV_STATE: "/из/окружения.json"}, home=HOME
        )
        assert result == Path("/из/окружения.json")

    def test_умолчание(self):
        result = resolve_state_path(None, system="Linux", env={}, home=HOME)
        assert result == HOME / ".config" / APP_NAME / STATE_FILENAME

    def test_пустая_переменная_не_считается(self):
        result = resolve_state_path(None, system="Linux", env={ENV_STATE: ""}, home=HOME)
        assert result == HOME / ".config" / APP_NAME / STATE_FILENAME

    def test_тильда_раскрывается(self):
        result = resolve_state_path("~/state.json", system="Linux", env={}, home=HOME)
        assert "~" not in str(result)
