"""Поведение команд: что уходит в stdout, что в stderr, с каким кодом возврата.

Браузер здесь подменяется: проверяется не то, как Playwright ходит по страницам,
а договорённости, на которые опирается вызывающий скрипт.
"""

from __future__ import annotations

import json

import pytest

from twogis_token import auth_api, browser, cli
from twogis_token.auth_api import CheckResult
from twogis_token.errors import EXIT_AUTH, EXIT_NETWORK, EXIT_OK, EXIT_TOKEN
from twogis_token.state import ENV_STATE

TOKEN = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def сессия(tmp_path, monkeypatch):
    """Файл сессии на месте — путь к нему берётся из переменной окружения."""
    state = tmp_path / "storage_state.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ENV_STATE, str(state))
    return state


@pytest.fixture
def браузер_отдаёт_токен(monkeypatch):
    async def fake_capture(state_path, *, url, timeout, headless):
        return browser.Capture(TOKEN, "websocket")

    monkeypatch.setattr(browser, "capture_token", fake_capture)


def всё_из_stdout(capsys) -> str:
    return capsys.readouterr().out


class TestGet:
    def test_печатает_только_токен(self, сессия, браузер_отдаёт_токен, capsys):
        assert cli.main(["get"]) == EXIT_OK
        assert capsys.readouterr().out == TOKEN + "\n"

    def test_json_содержит_источник(self, сессия, браузер_отдаёт_токен, capsys):
        assert cli.main(["get", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["token"] == TOKEN
        assert payload["source"] == "websocket"

    def test_out_пишет_в_файл_и_молчит_в_stdout(
        self, сессия, браузер_отдаёт_токен, tmp_path, capsys
    ):
        target = tmp_path / "вложенный" / "token.txt"
        assert cli.main(["get", "--out", str(target)]) == EXIT_OK
        captured = capsys.readouterr()
        assert target.read_text(encoding="utf-8").strip() == TOKEN
        assert captured.out == ""
        assert TOKEN not in captured.err

    def test_out_с_json_не_печатает_токен(self, сессия, браузер_отдаёт_токен, tmp_path, capsys):
        target = tmp_path / "token.txt"
        assert cli.main(["get", "--out", str(target), "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert "token" not in payload
        assert payload["out"] == str(target)

    def test_нет_сессии(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(ENV_STATE, str(tmp_path / "нет.json"))
        assert cli.main(["get"]) == EXIT_AUTH
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "login" in captured.err

    def test_сессия_истекла(self, сессия, monkeypatch, capsys):
        async def fake_capture(state_path, *, url, timeout, headless):
            raise browser.SessionExpired("токен не появился — войди заново: 2gis-token login")

        monkeypatch.setattr(browser, "capture_token", fake_capture)
        assert cli.main(["get"]) == EXIT_AUTH
        assert "login" in capsys.readouterr().err


class TestCheck:
    def test_живой(self, monkeypatch, capsys):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=True, detail="жив, аккаунт Дмитрий", status=200),
        )
        assert cli.main(["check", "--token", TOKEN]) == EXIT_OK
        assert "жив" in всё_из_stdout(capsys)

    def test_токен_не_печатается(self, monkeypatch, capsys):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=True, detail="жив", status=200),
        )
        cli.main(["check", "--token", TOKEN])
        assert TOKEN not in capsys.readouterr().out

    def test_протухший(self, monkeypatch, capsys):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=False, detail="протух", status=401, expired=True),
        )
        assert cli.main(["check", "--token", TOKEN]) == EXIT_AUTH

    def test_нет_сети(self, monkeypatch):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=False, detail="сети нет", reachable=False),
        )
        assert cli.main(["check", "--token", TOKEN]) == EXIT_NETWORK

    def test_странный_ответ_сервиса(self, monkeypatch):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=False, detail="HTTP 500", status=500),
        )
        assert cli.main(["check", "--token", TOKEN]) == EXIT_TOKEN

    def test_мусор_вместо_токена_не_идёт_в_сеть(self, monkeypatch, capsys):
        def нельзя(token, **kw):  # pragma: no cover — не должен вызваться
            raise AssertionError("запрос не нужен")

        monkeypatch.setattr(auth_api, "check_token", нельзя)
        assert cli.main(["check", "--token", "не токен"]) == EXIT_TOKEN
        assert "40 знаков" in capsys.readouterr().err

    def test_токен_из_stdin(self, monkeypatch, capsys):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=token == TOKEN, detail="жив", status=200),
        )
        assert cli.main(["check", "--token", "-"]) == EXIT_OK

    def test_без_токена_берёт_из_браузера(self, сессия, браузер_отдаёт_токен, monkeypatch, capsys):
        monkeypatch.setattr(
            auth_api,
            "check_token",
            lambda token, **kw: CheckResult(alive=token == TOKEN, detail="жив", status=200),
        )
        assert cli.main(["check", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["source"] == "websocket"
        assert payload["alive"] is True
        assert TOKEN not in json.dumps(payload)


class TestLogin:
    def test_ничего_не_печатает_в_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(ENV_STATE, str(tmp_path / "state.json"))

        async def fake_login(state_path, *, url, timeout, on_message=None):
            if on_message:
                on_message("окно открыто")
            return browser.Capture(TOKEN, "websocket")

        monkeypatch.setattr(browser, "interactive_login", fake_login)
        assert cli.main(["login"]) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ""
        assert TOKEN not in captured.err


class TestРазборАргументов:
    def test_без_команды(self):
        with pytest.raises(SystemExit):
            cli.main([])

    def test_версия(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["--version"])
        assert exit_info.value.code == 0
