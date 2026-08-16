#!/usr/bin/env python3
"""Наблюдение за сроком жизни токена 2ГИС.

Вопрос «сколько живёт токен» из одного замера не решается — нужна история.
Известно только, что токен от 2026-07-26 работал 2026-08-16, то есть живёт
не меньше трёх недель. Верхняя граница не установлена, и от неё зависит,
нужна ли вообще автоматика: если токен живёт полгода, входить раз в полгода
руками — нормально.

    # разовая проверка (для cron или Планировщика задач)
    python tools/token_watch.py --once

    # непрерывно, раз в 6 часов
    python tools/token_watch.py --interval 6

    # что накопилось
    python tools/token_watch.py --report

Токен берётся из ``data/token.json`` (``{"token": "..."}``) либо из stdin.
Каталог ``data/`` в .gitignore — секретам в репозитории не место.

Важная оговорка про толкование: если у 2ГИС срок скользящий и продлевается
активностью, то программа, постоянно ходящая с этим токеном, будет держать
его живым сколь угодно долго. Наблюдение при работающем потребителе и без
него — это два разных опыта, и путать их результаты нельзя.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from twogis_token.auth_api import check_token  # noqa: E402
from twogis_token.tokens import fingerprint, is_token  # noqa: E402

TOKEN_FILE = ROOT / "data" / "token.json"
LOG_FILE = ROOT / "data" / "token_watch.jsonl"


def load_token(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.read().strip()
    if not TOKEN_FILE.exists():
        raise SystemExit(
            f"Нет {TOKEN_FILE}.\n"
            f"  2gis-token get --out {TOKEN_FILE.parent / 'token.txt'}\n"
            "либо положи туда JSON вида {\"token\": \"...\"}"
        )
    text = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if text.startswith("{"):
        return str(json.loads(text)["token"]).strip()
    return text


def check(token: str) -> dict:
    result = check_token(token)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "alive": result.alive,
        "reachable": result.reachable,
        "detail": result.detail[:200],
        "token": fingerprint(token),
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def report() -> int:
    if not LOG_FILE.exists():
        print("История пуста — запусти хотя бы одну проверку.")
        return 1

    records = [
        json.loads(line)
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        print("История пуста.")
        return 1

    def moment(record: dict) -> datetime:
        return datetime.fromisoformat(record["ts"])

    first, last = moment(records[0]), moment(records[-1])
    # Недоступная сеть — это не смерть токена, и в статистику её брать нельзя.
    alive = [r for r in records if r["alive"]]
    dead = [r for r in records if not r["alive"] and r.get("reachable", True)]

    print(f"проверок:        {len(records)}")
    print(f"первая:          {first.astimezone().isoformat(timespec='minutes')}")
    print(f"последняя:       {last.astimezone().isoformat(timespec='minutes')}")
    print(f"окно наблюдения: {(last - first).total_seconds() / 86400:.1f} суток")

    if alive:
        last_alive = moment(alive[-1])
        print(f"последний раз жив: {last_alive.astimezone().isoformat(timespec='minutes')}")
        print(
            "подтверждённое время жизни: не менее "
            f"{(last_alive - first).total_seconds() / 86400:.1f} суток"
        )
    if dead:
        first_dead = moment(dead[0])
        print(f"\nПЕРВЫЙ ОТКАЗ: {first_dead.astimezone().isoformat(timespec='minutes')}")
        print(f"  причина: {dead[0]['detail']}")
        if alive:
            gap = (first_dead - moment(alive[-1])).total_seconds() / 3600
            print(f"  умер в промежутке шириной {gap:.1f} ч после последней удачной проверки")
    else:
        print("\nотказов не было — токен всё ещё жив")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--once", action="store_true", help="одна проверка и выход")
    parser.add_argument("--interval", type=float, default=6.0, help="часы между проверками")
    parser.add_argument("--report", action="store_true", help="показать накопленное")
    parser.add_argument("--stdin", action="store_true", help="взять токен из stdin")
    args = parser.parse_args()

    if args.report:
        return report()

    token = load_token(args.stdin)
    if not is_token(token):
        raise SystemExit("это не похоже на токен 2ГИС: ожидается 40 знаков 0-9 и a-f")

    while True:
        record = check(token)
        status = "жив" if record["alive"] else "МЁРТВ"
        print(f"{record['ts']}  {status}  {record['detail']}", flush=True)
        if not record["alive"] and record["reachable"]:
            print("\nТокен перестал приниматься — запусти --report и посмотри окно.")
            return 1
        if args.once:
            return 0
        time.sleep(args.interval * 3600)


if __name__ == "__main__":
    raise SystemExit(main())
