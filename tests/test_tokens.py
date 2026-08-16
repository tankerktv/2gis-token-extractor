"""Опознание токена и его поиск в строках."""

from __future__ import annotations

import pytest

from twogis_token.tokens import (
    TokenCollector,
    find_token,
    fingerprint,
    is_token,
    redact,
    token_from_headers,
    token_from_url,
)

TOKEN = "0123456789abcdef0123456789abcdef01234567"
OTHER = "ffffffffffffffffffffffffffffffffffffffff"

WS_URL = (
    "wss://zond.api.2gis.ru/api/1.1/user/ws"
    "?appVersion=6.31.0"
    "&channels=markers,sharing,routes"
    f"&token={TOKEN}"
)


class TestIsToken:
    def test_настоящий_токен(self):
        assert is_token(TOKEN)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            None,
            123,
            TOKEN[:-1],  # 39 знаков
            TOKEN + "0",  # 41 знак
            TOKEN.upper(),  # 2ГИС отдаёт нижний регистр
            TOKEN[:-1] + "g",  # не шестнадцатеричный знак
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop",  # JWT
            f" {TOKEN}",  # пробел — это уже не токен целиком
        ],
    )
    def test_не_токен(self, value):
        assert not is_token(value)


class TestFindToken:
    def test_в_шуме(self):
        assert find_token(f"чепуха token={TOKEN}&ещё чепуха") == TOKEN

    def test_ничего(self):
        assert find_token("никаких токенов тут нет") is None

    def test_не_строка(self):
        assert find_token(None) is None

    def test_длинная_шестнадцатеричная_строка_не_режется(self):
        """Границы слова: из 41 знака нельзя выкусить первые 40 и выдать за токен."""
        assert find_token(TOKEN + "0") is None
        assert find_token("0" + TOKEN) is None


class TestTokenFromUrl:
    def test_адрес_сокета(self):
        assert token_from_url(WS_URL) == TOKEN

    def test_access_token(self):
        url = f"https://api.auth.2gis.com/2.1/users/me?access_token={TOKEN}"
        assert token_from_url(url) == TOKEN

    def test_чужой_параметр_с_похожим_значением(self):
        """sha1 картинки в параметре hash токеном не считается."""
        assert token_from_url(f"https://2gis.ru/tile.png?hash={OTHER}") is None

    def test_без_параметров(self):
        assert token_from_url("https://2gis.ru/") is None

    def test_испорченное_значение(self):
        assert token_from_url(f"wss://zond.api.2gis.ru/ws?token={TOKEN[:-1]}") is None

    def test_не_строка(self):
        assert token_from_url(None) is None


class TestTokenFromHeaders:
    def test_x_token(self):
        assert token_from_headers({"X-Token": TOKEN}) == TOKEN

    def test_регистр_имени_не_важен(self):
        assert token_from_headers({"x-token": TOKEN}) == TOKEN

    def test_bearer(self):
        assert token_from_headers({"Authorization": f"Bearer {TOKEN}"}) == TOKEN

    def test_посторонний_заголовок_игнорируется(self):
        assert token_from_headers({"If-None-Match": OTHER}) is None

    def test_пусто(self):
        assert token_from_headers(None) is None
        assert token_from_headers({}) is None


class TestFingerprint:
    def test_токена_внутри_нет(self):
        assert TOKEN not in fingerprint(TOKEN)

    def test_разные_токены_различимы(self):
        assert fingerprint(TOKEN) != fingerprint(OTHER)

    def test_одинаковые_совпадают(self):
        assert fingerprint(TOKEN) == fingerprint(TOKEN)

    def test_пусто(self):
        assert fingerprint("") == "<пусто>"
        assert fingerprint(None) == "<пусто>"

    def test_посторонняя_строка_помечается(self):
        assert "строка" in fingerprint("не токен вовсе")


def test_redact_убирает_токен_из_текста():
    text = redact(f"открыт сокет {WS_URL}")
    assert TOKEN not in text
    assert "<ТОКЕН>" in text


class TestCollector:
    def test_пустой(self):
        collector = TokenCollector()
        assert not collector
        assert collector.token is None
        assert collector.source is None

    def test_сокет(self):
        collector = TokenCollector()
        collector.offer_websocket_url(WS_URL)
        assert collector.token == TOKEN
        assert collector.source == "websocket"

    def test_надёжный_источник_вытесняет_сомнительный(self):
        """Сначала пришёл чужой sha1 из текста, потом настоящий токен из сокета."""
        collector = TokenCollector()
        collector.offer_text(f"сборка {OTHER}")
        assert collector.token == OTHER
        collector.offer_websocket_url(WS_URL)
        assert collector.token == TOKEN
        assert collector.source == "websocket"

    def test_сомнительный_не_вытесняет_надёжный(self):
        collector = TokenCollector()
        collector.offer_websocket_url(WS_URL)
        collector.offer_text(f"сборка {OTHER}")
        assert collector.token == TOKEN

    def test_при_равной_надёжности_остаётся_первый(self):
        collector = TokenCollector()
        collector.offer_request_url(f"https://2gis.ru/a?token={TOKEN}")
        collector.offer_request_url(f"https://2gis.ru/b?token={OTHER}")
        assert collector.token == TOKEN

    def test_мусор_не_принимается(self):
        collector = TokenCollector()
        collector.offer_text("никаких токенов")
        collector.offer_request_url("https://2gis.ru/")
        collector.offer_headers({"Accept": "*/*"})
        collector.offer(TOKEN, "неизвестный источник")
        assert not collector

    def test_заголовки(self):
        collector = TokenCollector()
        collector.offer_headers({"X-Token": TOKEN})
        assert collector.source == "header"

    def test_сокет_без_параметра_но_с_токеном_в_адресе(self):
        collector = TokenCollector()
        collector.offer_websocket_url(f"wss://zond.api.2gis.ru/api/1.1/user/ws/{TOKEN}")
        assert collector.token == TOKEN
