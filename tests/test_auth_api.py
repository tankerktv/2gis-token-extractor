"""Разбор ответа ``users/me`` и проверка токена."""

from __future__ import annotations

import json
import urllib.error

from twogis_token.auth_api import account_name, check_token, describe, parse_users_me

TOKEN = "0123456789abcdef0123456789abcdef01234567"


class TestParseUsersMe:
    def test_живой_токен_с_именем(self):
        result = parse_users_me(200, json.dumps({"id": "42", "display_name": "Дмитрий"}))
        assert result.alive
        assert result.account == "Дмитрий"
        assert "Дмитрий" in result.detail

    def test_живой_токен_в_обёртке(self):
        result = parse_users_me(200, json.dumps({"meta": {"code": 200}, "result": {"name": "Аня"}}))
        assert result.alive
        assert result.account == "Аня"

    def test_живой_токен_без_имени(self):
        result = parse_users_me(200, json.dumps({"anything": True}))
        assert result.alive
        assert result.account is None
        assert result.detail == "жив"

    def test_двести_но_не_json(self):
        result = parse_users_me(200, "<html>сюрприз</html>")
        assert not result.alive
        assert not result.expired
        assert "JSON" in result.detail

    def test_401_это_протух(self):
        result = parse_users_me(401, "")
        assert not result.alive
        assert result.expired
        assert "login" in result.detail

    def test_403_это_тоже_протух(self):
        assert parse_users_me(403, "").expired

    def test_пятисотка_не_повод_идти_логиниться(self):
        result = parse_users_me(503, "")
        assert not result.alive
        assert not result.expired
        assert result.reachable

    def test_неожиданный_код(self):
        result = parse_users_me(404, "")
        assert not result.alive
        assert not result.expired
        assert "404" in result.detail


class TestAccountName:
    def test_порядок_ключей(self):
        assert account_name({"id": "42", "name": "Аня", "display_name": "Дмитрий"}) == "Дмитрий"

    def test_числовой_идентификатор(self):
        assert account_name({"id": 42}) == "42"

    def test_почты_в_имени_нет(self):
        """Почта — лишнее в выводе, который уезжает в логи."""
        assert account_name({"email": "a@b.ru"}) is None

    def test_не_словарь(self):
        assert account_name(["что-то"]) is None
        assert account_name(None) is None

    def test_пустая_строка_не_имя(self):
        assert account_name({"display_name": "  ", "id": "42"}) == "42"


class TestCheckToken:
    def test_токен_уходит_в_параметре(self):
        seen = {}

        def fetch(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return 200, json.dumps({"display_name": "Дмитрий"})

        result = check_token(TOKEN, timeout=3.0, fetch=fetch)
        assert result.alive
        assert f"access_token={TOKEN}" in seen["url"]
        assert seen["timeout"] == 3.0

    def test_пустой_токен_не_ходит_в_сеть(self):
        def fetch(url, timeout):  # pragma: no cover — не должен вызваться
            raise AssertionError("запрос не нужен")

        result = check_token("", fetch=fetch)
        assert not result.alive

    def test_нет_сети(self):
        def fetch(url, timeout):
            raise urllib.error.URLError("нет маршрута")

        result = check_token(TOKEN, fetch=fetch)
        assert not result.alive
        assert not result.reachable
        assert not result.expired

    def test_обрыв_соединения(self):
        def fetch(url, timeout):
            raise OSError("соединение сброшено")

        assert not check_token(TOKEN, fetch=fetch).reachable

    def test_протухший(self):
        result = check_token(TOKEN, fetch=lambda url, timeout: (401, ""))
        assert result.expired
        assert result.reachable


def test_describe_не_печатает_токен():
    result = check_token(TOKEN, fetch=lambda url, timeout: (401, ""))
    text = describe(TOKEN, result)
    assert TOKEN not in text
    assert "sha1=" in text
