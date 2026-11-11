"""Read back task runs (db/191_task_runs.sql, docs/goal_router.md P1-10). Read-only.

    python scripts/show_runs.py --task demo-1
    python scripts/show_runs.py --goal web --since 1h
    python scripts/show_runs.py --arm tool --limit 20 --json

Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from app.tool_routing.runs import ARMS, list_runs

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def seconds(text: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", (text or "").strip())
    if not m:
        raise SystemExit(f"--since expects a number and a unit (30m, 1h, 7d), got {text!r}")
    return int(m.group(1)) * UNITS[m.group(2)]


async def run(args: argparse.Namespace) -> int:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = args.dsn or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        rows = await list_runs(conn, task_id=args.task, goal_id=args.goal, arm=args.arm,
                               since_s=seconds(args.since) if args.since else None, limit=args.limit)
    finally:
        await conn.close()
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no runs")
        return 1
    print(f"{'task':<14} {'arm':<9} {'#':>2} {'status':<6} {'latency':>8} {'cost $':>9} {'score':>5}  {'goal':<26} "
          f"{'tool / procedure':<34} error")
    for r in rows:
        what = r["card_id"] or r["procedure_id"] or "-"
        lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "-"
        cost = f"{r['cost_usd']:.6f}" if r["cost_usd"] is not None else "-"
        score = f"{r['score']:.2f}" if r["score"] is not None else "-"
        print(f"{r['task_id'][:14]:<14} {r['arm']:<9} {r['attempt']:>2} {r['status']:<6} {lat:>8} {cost:>9} {score:>5}  "
              f"{(r['goal_id'] or '-')[:26]:<26} {what[:34]:<34} {r['error_class'] or ''}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Show task runs (both arms).")
    ap.add_argument("--task")
    ap.add_argument("--goal")
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--since", help="30m, 1h, 7d")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
