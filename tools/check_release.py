#!/usr/bin/env python3
"""Проверяет, что репозиторий готов к тегу.

    python tools/check_release.py v0.1.0

Ловит две ошибки, которые иначе обнаруживаются уже после выпуска:

* версия в коде не совпала с тегом — пользователь ставит одно, а ``--version``
  показывает другое;
* в CHANGELOG нет раздела под эту версию — релиз уезжает с пустым описанием.

Версия хранится в одном месте — ``src/twogis_token/__init__.py``. Здесь она
читается регулярным выражением, а не импортом: скрипт должен работать в голом
контейнере, где пакет не установлен.
"""

from __future__ import annotations

import pathlib
import re
import sys

VERSION_RE = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "twogis_token" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def version_from_source(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(1) if match else None


def problems(tag: str, source: str, changelog: str) -> list[str]:
    """Список претензий. Пустой список — можно выпускать."""
    found: list[str] = []
    version = version_from_source(source)

    if version is None:
        found.append("в __init__.py не нашлось __version__")
    elif not SEMVER_RE.match(version):
        found.append(f"версия {version!r} не вида X.Y.Z")

    if tag:
        wanted = tag.lstrip("v")
        if version is not None and wanted != version:
            found.append(f"тег {tag} не совпадает с версией в коде ({version})")
        if f"## [{wanted}]" not in changelog:
            found.append(f"в CHANGELOG.md нет раздела '## [{wanted}]'")

    return found


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    found = problems(tag, SOURCE.read_text(encoding="utf-8"), CHANGELOG.read_text(encoding="utf-8"))
    for problem in found:
        print("ОШИБКА:", problem)
    if not found:
        print("готово к выпуску" + (f" {tag}" if tag else ""))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
