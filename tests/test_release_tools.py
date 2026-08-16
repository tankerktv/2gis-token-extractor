"""Обвязка выпуска: раздел CHANGELOG и сверка версии с тегом.

Эти две проверки существуют потому, что ошибка в них обнаруживается уже после
того, как релиз опубликован.
"""

from __future__ import annotations

import pathlib

import pytest
from tools.changelog_section import section
from tools.check_release import problems, version_from_source

ROOT = pathlib.Path(__file__).resolve().parents[1]

CHANGELOG = """# Изменения

## [Не выпущено]

## [0.2.0] — 2026-09-01

### Добавлено

- Что-то новое — со стрелкой → и длинным тире.

## [0.1.0] — 2026-08-17

Первый выпуск.
"""

SOURCE = '__version__ = "0.1.0"\n'


class TestChangelogSection:
    def test_берёт_нужный_раздел(self):
        assert "Что-то новое" in section(CHANGELOG, "0.2.0")

    def test_не_прихватывает_соседний(self):
        assert "Первый выпуск" not in section(CHANGELOG, "0.2.0")

    def test_заголовок_не_попадает_в_текст(self):
        assert "## [0.2.0]" not in section(CHANGELOG, "0.2.0")

    def test_последний_раздел(self):
        assert section(CHANGELOG, "0.1.0") == "Первый выпуск."

    def test_нет_раздела(self):
        with pytest.raises(SystemExit):
            section(CHANGELOG, "9.9.9")

    def test_пустой_раздел(self):
        with pytest.raises(SystemExit):
            section("## [0.3.0] — 2026-10-01\n\n## [0.2.0]\nтекст\n", "0.3.0")


class TestCheckRelease:
    def test_версия_из_исходника(self):
        assert version_from_source(SOURCE) == "0.1.0"

    def test_версии_нет(self):
        assert version_from_source("ничего тут нет") is None

    def test_всё_совпадает(self):
        assert problems("v0.1.0", SOURCE, CHANGELOG) == []

    def test_тег_не_совпал_с_версией(self):
        found = problems("v0.2.0", SOURCE, CHANGELOG)
        assert any("не совпадает" in problem for problem in found)

    def test_нет_раздела_в_changelog(self):
        found = problems("v0.3.0", '__version__ = "0.3.0"\n', CHANGELOG)
        assert any("CHANGELOG" in problem for problem in found)

    def test_версия_не_семантическая(self):
        found = problems("", '__version__ = "0.1"\n', CHANGELOG)
        assert any("X.Y.Z" in problem for problem in found)

    def test_без_тега_проверяется_только_версия(self):
        assert problems("", SOURCE, CHANGELOG) == []


class TestНастоящийРепозиторий:
    """Файлы репозитория должны проходить те же проверки прямо сейчас."""

    def test_версия_и_changelog_согласованы(self):
        source = (ROOT / "src" / "twogis_token" / "__init__.py").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        version = version_from_source(source)
        assert problems(f"v{version}", source, changelog) == []
