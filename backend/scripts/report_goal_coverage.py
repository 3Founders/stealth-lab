"""Report how well the tool catalog covers the Goal graph (db/186, P1-03).

    python scripts/report_goal_coverage.py
    python scripts/report_goal_coverage.py --limit 30

Read-only. Prints total cards and mappings, cards with no Goal, K000 Goals with no card, and the
best-covered Goals. Used by the mapper's `--report` flag.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


async def report(conn, *, limit: int = 20) -> None:
    cards = await conn.fetchval("SELECT count(*) FROM tool_cards")
    mappings = await conn.fetchval("SELECT count(*) FROM tool_card_goals")
    orphans = await conn.fetchval(
        """SELECT count(*) FROM tool_cards t
           WHERE NOT EXISTS (SELECT 1 FROM tool_card_goals m WHERE m.card_id = t.id)""")
    zero = await conn.fetch(
        """SELECT g.canonical_name
           FROM goals g
           WHERE g.t_invalid IS NULL AND g.home_shard_id = 'K000'
             AND NOT EXISTS (SELECT 1 FROM tool_card_goals m WHERE m.goal_id = g.id)
           ORDER BY g.canonical_name""")
    top = await conn.fetch(
        """SELECT g.canonical_name, count(*) AS n
           FROM tool_card_goals m JOIN goals g ON g.id = m.goal_id
           GROUP BY g.canonical_name ORDER BY n DESC, g.canonical_name LIMIT $1""", limit)

    print(f"cards:    {cards}")
    print(f"mappings: {mappings}")
    print(f"cards with no goal: {orphans}")
    print(f"K000 goals with no card: {len(zero)}")
    for row in zero[:20]:
        print(f"  - {row['canonical_name']}")
    print(f"top {limit} goals by cards:")
    for row in top:
        print(f"  {row['n']:>7}  {row['canonical_name']}")


async def run(limit: int) -> None:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        await report(conn, limit=limit)
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Report card-to-goal coverage.")
    ap.add_argument("--limit", type=int, default=20, help="how many top goals to show")
    args = ap.parse_args()
    asyncio.run(run(args.limit))


if __name__ == "__main__":
    main()
