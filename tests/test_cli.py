"""Поведение команд: что уходит в stdout, что в stderr, с каким кодом возврата.

Браузер здесь подменяется: проверяется не то, как Playwright ходит по страницам,
а договорённости, на которые опирается вызывающий скрипт.
"""

from __future__ import annotations

import json

import pytest

from twogis_token import auth_api, browser, cli, gui
from twogis_token.auth_api import CheckResult
from twogis_token.errors import (
    EXIT_AUTH,
    EXIT_ENVIRONMENT,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_TOKEN,
    WindowUnavailable,
)
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

    def test_двойной_клик_открывает_окно(self, monkeypatch, capsys):
        """Кликом запускают те, кому терминал барьер, — им окно, а не справка."""
        запуски = []

        def нельзя(*args):  # pragma: no cover — не должен вызваться
            raise AssertionError("окно открылось — ждать Enter незачем")

        monkeypatch.setattr(cli, "start_window_detached", запуски.append)
        monkeypatch.setattr("builtins.input", нельзя)
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: True)

        assert cli.main([]) == EXIT_OK
        assert len(запуски) == 1
        assert capsys.readouterr().out == ""

    def test_без_окна_клик_даёт_подсказку_и_ждёт_enter(self, monkeypatch, capsys):
        """Окна не будет — консоль всё равно не должна закрыться молча."""
        нажатия = []

        def окна_нет(executable):
            raise WindowUnavailable("no display")

        monkeypatch.setattr(cli, "start_window_detached", окна_нет)
        monkeypatch.setattr("builtins.input", lambda *args: нажатия.append(1) or "")
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: True)

        assert cli.main([]) == cli.EXIT_USAGE
        assert нажатия == [1]
        ошибки = capsys.readouterr().err
        assert "no display" in ошибки
        assert "Press Enter" in ошибки

    def test_без_окна_и_с_закрытым_вводом_не_падает(self, monkeypatch):
        def окна_нет(executable):
            raise WindowUnavailable("no display")

        def конец_ввода(*args):
            raise EOFError

        monkeypatch.setattr(cli, "start_window_detached", окна_нет)
        monkeypatch.setattr("builtins.input", конец_ввода)
        monkeypatch.setattr(cli, "window_closes_on_exit", lambda **kw: True)
        assert cli.main([]) == cli.EXIT_USAGE

    def test_перезапуск_идёт_с_командой(self):
        """Без команды копия снова сочла бы себя запущенной кликом — и так без конца."""
        команда = cli.window_command(r"C:\Downloads\2gis-token.exe")
        assert команда == [r"C:\Downloads\2gis-token.exe", "gui"]


class TestОкружениеПерезапуска:
    """Сборка сообщает потомкам, где лежит её распакованная копия.

    Потомок с такой переменной не распаковывается сам, а берёт ту же папку —
    и когда первая копия выходит, она эту папку стирает. Поймано на живой
    сборке: окно открылось, а на кнопку ответило «в этой копии нет Playwright»,
    хотя та же сборка из консоли работала.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "_PYI_APPLICATION_HOME_DIR",
            "_PYI_ARCHIVE_FILE",
            "_PYI_PARENT_PROCESS_LEVEL",
            "_MEIPASS2",
        ],
    )
    def test_служебные_переменные_сборки_не_передаются(self, name):
        assert name not in cli.child_environment({name: "C:\\Temp\\_MEI123", "PATH": "/usr/bin"})

    def test_регистр_не_спасает(self):
        assert cli.child_environment({"_pyi_archive_file": "x"}) == {}

    def test_всё_остальное_передаётся(self):
        окружение = {"PATH": "/usr/bin", "TWOGIS_TOKEN_PROFILE": "работа", "_PYI_X": "y"}
        assert cli.child_environment(окружение) == {
            "PATH": "/usr/bin",
            "TWOGIS_TOKEN_PROFILE": "работа",
        }

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


class TestКомандаОкна:
    @pytest.fixture
    def окно(self, monkeypatch):
        вызовы = []

        def run(**options):
            вызовы.append(options)
            return 0

        monkeypatch.setattr(gui, "run", run)
        return вызовы

    def test_открывает_окно(self, окно):
        assert cli.main(["gui"]) == EXIT_OK
        assert len(окно) == 1
        assert окно[0]["language"] is None  # язык системы решает само окно

    def test_язык_по_просьбе(self, окно):
        assert cli.main(["gui", "--lang", "ru"]) == EXIT_OK
        assert окно[0]["language"] == "ru"

    def test_незнакомый_язык_отвергается(self, окно):
        with pytest.raises(SystemExit):
            cli.main(["gui", "--lang", "de"])
        assert окно == []

    def test_профиль_доезжает_до_окна(self, окно):
        assert cli.main(["gui", "--profile", "работа"]) == EXIT_OK
        путь = str(окно[0]["state_path"])
        assert "profiles" in путь
        assert "работа" in путь

    def test_из_терминала_консоль_не_прячется(self, окно):
        """Иначе спрятался бы терминал самого человека."""
        cli.main(["gui"])
        assert "hide_console" not in окно[0]

    def test_нет_экрана_внятная_ошибка(self, monkeypatch, capsys):
        def окна_нет(**options):
            raise WindowUnavailable("cannot open a window here")

        monkeypatch.setattr(gui, "run", окна_нет)
        assert cli.main(["gui"]) == EXIT_ENVIRONMENT
        assert "cannot open a window" in capsys.readouterr().err


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
