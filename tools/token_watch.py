#!/usr/bin/env python3
"""Watch how long a 2GIS token actually lives.

One measurement cannot answer that question — it takes a history. All that is
known so far: a token issued on 2026-07-26 still worked on 2026-08-16, so it
lives at least three weeks. The upper bound is unknown, and it decides whether
any refresh machinery is worth building at all.

    python tools/token_watch.py --once      # one check, for cron or Task Scheduler
    python tools/token_watch.py --interval 6
    python tools/token_watch.py --report

The token is read from data/token.json ({"token": "..."}) or from stdin.
data/ is in .gitignore — secrets do not belong in the repository.

One caveat about reading the results: if the lifetime slides forward on use,
a program that keeps talking to 2GIS will keep its token alive indefinitely,
and the token will only die during idleness. Watching with an active consumer
and without one are two different experiments; their results cannot be mixed.
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
            f"No {TOKEN_FILE}.\n"
            f"  2gis-token get --out {TOKEN_FILE.parent / 'token.txt'}\n"
            'or put a JSON file there: {"token": "..."}'
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
        print("No history yet — run at least one check.")
        return 1

    records = [
        json.loads(line)
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        print("No history yet.")
        return 1

    def moment(record: dict) -> datetime:
        return datetime.fromisoformat(record["ts"])

    first, last = moment(records[0]), moment(records[-1])
    # Недоступная сеть — это не смерть токена, и в статистику её брать нельзя.
    alive = [r for r in records if r["alive"]]
    dead = [r for r in records if not r["alive"] and r.get("reachable", True)]

    print(f"checks:          {len(records)}")
    print(f"first:           {first.astimezone().isoformat(timespec='minutes')}")
    print(f"last:            {last.astimezone().isoformat(timespec='minutes')}")
    print(f"observed window: {(last - first).total_seconds() / 86400:.1f} days")

    if alive:
        last_alive = moment(alive[-1])
        print(f"last seen alive: {last_alive.astimezone().isoformat(timespec='minutes')}")
        print(
            "confirmed lifetime: at least "
            f"{(last_alive - first).total_seconds() / 86400:.1f} days"
        )
    if dead:
        first_dead = moment(dead[0])
        print(f"\nFIRST REJECTION: {first_dead.astimezone().isoformat(timespec='minutes')}")
        print(f"  reason: {dead[0]['detail']}")
        if alive:
            gap = (first_dead - moment(alive[-1])).total_seconds() / 3600
            print(f"  died within a {gap:.1f} h window after the last good check")
    else:
        print("\nno rejections yet — the token is still alive")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--once", action="store_true", help="one check, then exit")
    parser.add_argument("--interval", type=float, default=6.0, help="hours between checks")
    parser.add_argument("--report", action="store_true", help="show what has piled up")
    parser.add_argument("--stdin", action="store_true", help="read the token from stdin")
    args = parser.parse_args()

    if args.report:
        return report()

    token = load_token(args.stdin)
    if not is_token(token):
        raise SystemExit(
            "that does not look like a 2GIS token: expected 40 characters, 0-9 and a-f"
        )

    while True:
        record = check(token)
        status = "alive" if record["alive"] else "DEAD"
        print(f"{record['ts']}  {status}  {record['detail']}", flush=True)
        if not record["alive"] and record["reachable"]:
            print("\nThe token stopped being accepted — run --report and look at the window.")
            return 1
        if args.once:
            return 0
        time.sleep(args.interval * 3600)


if __name__ == "__main__":
    raise SystemExit(main())
