"""Refresh the materialized per-Goal tool ranking (db/200, docs/goal_router.md P3-03).

    python scripts/refresh_goal_rankings.py
    python scripts/refresh_goal_rankings.py --goal web
    python scripts/refresh_goal_rankings.py --no-trust-refresh

Computes the P3-03 rank once per Goal (trust lower bound + exploration - cost - latency - risk),
stores the ordered rows, and prints how many were written. The router reads them; a Goal with no rows
falls back to the live ranker. Run it after new evidence (tool runs/comparisons), after the lifecycle
job, or after a policy/listing/pin change. `tool_trust` is refreshed first unless --no-trust-refresh.

Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


async def run(args: argparse.Namespace) -> int:
    url = args.dsn or dsn_from_env()
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    pool = await asyncpg.create_pool(url, min_size=1, max_size=max(2, args.concurrency + 2), timeout=60)
    try:
        from app.tool_routing import ranking

        written = await ranking.refresh(pool, goal=args.goal, trust_refresh=not args.no_trust_refresh,
                                        concurrency=args.concurrency)
        print(f"ranked rows: {written}")
        return 0
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh the materialized per-Goal tool ranking.")
    ap.add_argument("--goal", default=None, help="one Goal uuid or a canonical-name substring")
    ap.add_argument("--concurrency", type=int, default=6, help="Goals ranked at once (default 6)")
    ap.add_argument("--no-trust-refresh", action="store_true",
                    help="use the current tool_trust view instead of refreshing it first")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
