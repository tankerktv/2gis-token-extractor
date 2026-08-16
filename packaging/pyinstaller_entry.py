"""Точка входа для однофайловой сборки PyInstaller.

PyInstaller собирает скрипт, а не модуль, поэтому ``python -m twogis_token``
ему не годится. Файл существует только ради этого.
"""

from twogis_token.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
