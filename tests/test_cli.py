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
    async def fake_capture(state_path, *, url, timeout, headless, profile=None):
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
        async def fake_capture(state_path, *, url, timeout, headless, profile=None):
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
        assert "40 characters" in capsys.readouterr().err

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
    def test_без_команды_показывает_справку(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: False)
        assert cli.main([]) == cli.EXIT_USAGE
        captured = capsys.readouterr()
        assert "COMMAND" in captured.out
        assert "examples:" in captured.out

    def test_без_команды_из_терминала_не_ждёт(self, monkeypatch, capsys):
        """Обычный запуск без аргументов не должен повиснуть в ожидании Enter."""

        def нельзя(*args):  # pragma: no cover — не должен вызваться
            raise AssertionError("ждать Enter тут некого")

        monkeypatch.setattr("builtins.input", нельзя)
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: False)
        assert cli.main([]) == cli.EXIT_USAGE
        assert "Press Enter" not in capsys.readouterr().err

    def test_двойной_клик_ждёт_enter(self, monkeypatch, capsys):
        """Иначе окно закроется раньше, чем человек прочтёт хоть строчку."""
        нажатия = []
        monkeypatch.setattr("builtins.input", lambda *args: нажатия.append(1) or "")
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: True)
        assert cli.main([]) == cli.EXIT_USAGE
        assert нажатия == [1]
        assert "Press Enter" in capsys.readouterr().err

    def test_закрытый_ввод_не_роняет_программу(self, monkeypatch):
        def конец_ввода(*args):
            raise EOFError

        monkeypatch.setattr("builtins.input", конец_ввода)
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: True)
        assert cli.main([]) == cli.EXIT_USAGE

    def test_неизвестная_команда_по_прежнему_ошибка(self):
        with pytest.raises(SystemExit):
            cli.main(["нет-такой-команды"])

    def test_версия(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["--version"])
        assert exit_info.value.code == 0


class TestПрофили:
    def test_профиль_уводит_сессию_в_свой_каталог(self, браузер_отдаёт_токен, capsys):
        assert cli.main(["get", "--profile", "работа", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert "profiles" in payload["state"]
        assert "работа" in payload["state"]

    def test_профиль_важнее_переменной_окружения(self, сессия, браузер_отдаёт_токен, capsys):
        """Фикстура выставила TWOGIS_TOKEN_STATE — ключ команды должен победить."""
        assert cli.main(["get", "--profile", "работа", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert "работа" in payload["state"]
        assert str(сессия) != payload["state"]

    def test_негодное_имя_не_уводит_запись_из_каталога(self, браузер_отдаёт_токен, capsys):
        assert cli.main(["get", "--profile", "../побег"]) == EXIT_TOKEN
        assert "bad profile name" in capsys.readouterr().err

    def test_список_пуст(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "known_profiles", lambda: [])
        assert cli.main(["profiles"]) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "no saved profiles" in captured.err

    def test_список_по_строке_на_профиль(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "known_profiles", lambda: ["личный", "работа"])
        assert cli.main(["profiles"]) == EXIT_OK
        assert capsys.readouterr().out.split() == ["личный", "работа"]

    def test_список_машиночитаемо(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "known_profiles", lambda: ["работа"])
        assert cli.main(["profiles", "--json"]) == EXIT_OK
        assert json.loads(capsys.readouterr().out) == ["работа"]

    def test_подсказка_зовёт_войти_в_тот_же_профиль(self, capsys):
        """Совет без --profile увёл бы человека в другую сессию."""
        assert cli.main(["get", "--profile", "несуществующий"]) == EXIT_AUTH
        ошибка = capsys.readouterr().err
        assert "login --profile несуществующий" in ошибка


class TestДвойнойКлик:
    """Однофайловую сборку на Windows запускают кликом — и окно тут же закрывается."""

    def test_клик_по_сборке_на_windows(self):
        assert cli.window_closes_on_exit(frozen=True, platform="win32", interactive=True)

    @pytest.mark.parametrize(
        ("frozen", "platform", "interactive"),
        [
            (False, "win32", True),  # запуск из исходников или через pipx
            (True, "linux", True),  # консольное на Linux кликом не запускают
            (True, "darwin", True),
            (True, "win32", False),  # планировщик или конвейер: ждать Enter некого
        ],
    )
    def test_иначе_не_ждём(self, frozen, platform, interactive):
        assert not cli.window_closes_on_exit(
            frozen=frozen, platform=platform, interactive=interactive
        )

    def test_подсказка_называет_файл_как_он_есть(self):
        """Совет набрать 2gis-token не сработает, пока файл не переименован."""
        hint = cli.double_click_hint(r"C:\Users\Кто-то\Downloads\2gis-token-windows-x86_64.exe")
        assert "2gis-token-windows-x86_64.exe login" in hint
        assert "Downloads" not in hint

    def test_подсказка_говорит_что_делать_дальше(self):
        hint = cli.double_click_hint(r"C:\2gis-token.exe")
        for command in ("install-browser", "login", "get"):
            assert f"2gis-token.exe {command}" in hint
        assert "Press Enter" in hint
