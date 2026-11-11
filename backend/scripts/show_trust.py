"""Show trust from verified reuse per tool card (db/192_tool_trust.sql, docs/goal_router.md P3-01). Read-only, except
--refresh, which refreshes the materialized view first.

    python scripts/show_trust.py --goal web --limit 10
    python scripts/show_trust.py --goal web --refresh
    python scripts/show_trust.py --card registry:ai.exa/exa

Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from app.tool_routing import trust


async def run(args: argparse.Namespace) -> int:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = args.dsn or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        if args.refresh:
            await trust.refresh(conn)
        rows = await trust.rows(conn, goal=args.goal, card_ids=[args.card] if args.card else None, limit=args.limit)
    finally:
        await conn.close()
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no verified evidence yet" + (f" for goal {args.goal}" if args.goal else ""))
        return 1
    print(f"{'card':<46} {'wins/judged':>11} {'lower bd':>8} {'last':<5} {'last at':<19} {'compare':>7} {'use':>4} "
          f"{'trial':>5} {'paid':>5}")
    for r in rows:
        print(f"{r['card_id'][:46]:<46} {str(r['wins']) + '/' + str(r['judged']):>11} {r['lower_bound']:>8.3f} "
              f"{r['last_outcome'] or '-':<5} {str(r['last_at'])[:19]:<19} {r['from_comparisons']:>7} {r['from_use']:>4} "
              f"{r['trial_calls']:>5} {r['paid_calls']:>5}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Trust from verified reuse, per tool card.")
    ap.add_argument("--goal", help="a tool-goal key (web) or a Goal uuid")
    ap.add_argument("--card")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
