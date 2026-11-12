"""Show the materialized per-Goal tool ranking (db/200_goal_tool_rankings.sql). Read-only.

    python scripts/show_goal_ranking.py --goal web
    python scripts/show_goal_ranking.py --goal "Talk to people" --limit 10
    python scripts/show_goal_ranking.py --goal web --json

`rank_score` is trust lower bound + exploration - cost - latency - risk (and a retiring
penalty, when the card is being retired). The router reads this table only when
`GOAL_RANKING=trust`; otherwise it uses goal fit + stars. A Goal with no rows here falls
back to the live ranker (`goal_router._rank`).

Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values


async def run(args: argparse.Namespace) -> int:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = args.dsn or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        try:
            goal_id = uuid.UUID(args.goal)
        except ValueError:
            goal_id = None
        if goal_id is not None:
            goal = await conn.fetchrow(
                "SELECT id::text AS id, canonical_name FROM goals WHERE id = $1 AND t_invalid IS NULL",
                goal_id,
            )
        else:
            goal = await conn.fetchrow(
                "SELECT id::text AS id, canonical_name FROM goals "
                "WHERE t_invalid IS NULL AND canonical_name ILIKE '%' || $1 || '%' "
                "ORDER BY canonical_name LIMIT 1",
                args.goal,
            )
        if goal is None:
            raise SystemExit(f"no Goal matching {args.goal!r}")
        rows = await conn.fetch(
            """
            SELECT r.rank, r.rank_score, r.card_id, t.name, t.server, r.inputs
            FROM goal_tool_rankings r
            LEFT JOIN tool_cards t ON t.id = r.card_id
            WHERE r.goal_id = $1::uuid
            ORDER BY r.rank
            LIMIT $2
            """,
            goal["id"], max(1, min(int(args.limit), 500)),
        )
    finally:
        await conn.close()

    if args.json:
        print(json.dumps({
            "goal": {"id": goal["id"], "canonical_name": goal["canonical_name"]},
            "ranking": [
                {"rank": r["rank"], "rank_score": r["rank_score"], "card_id": r["card_id"],
                 "name": r["name"], "server": r["server"], "inputs": _inputs(r["inputs"])}
                for r in rows
            ],
        }, indent=2, default=str))
        return 0

    print(f"{goal['canonical_name']}  ({goal['id']})")
    if not rows:
        print("no materialized ranking: the router falls back to the live ranker for this Goal")
        return 0
    print("router reads this table only when GOAL_RANKING=trust\n")
    print(f"{'#':>3} {'score':>7} {'trust':>6} {'n':>4} {'expl':>6} {'cost':>6} {'lat':>6} {'risk':>6} {'fit':>5}  card")
    for r in rows:
        i = _inputs(r["inputs"])
        print(
            f"{r['rank']:>3} {float(r['rank_score'] or 0):>7.3f} {float(i.get('trust_lb') or 0):>6.3f} "
            f"{int(i.get('judged') or 0):>4} {float(i.get('exploration') or 0):>6.3f} "
            f"{float(i.get('cost_penalty') or 0):>6.3f} {float(i.get('latency_penalty') or 0):>6.3f} "
            f"{float(i.get('risk_penalty') or 0):>6.3f} {float(i.get('goal_fit') or 0):>5.2f}  "
            f"{r['card_id']}  {str(r['name'] or '')[:48]}"
        )
    return 0


def _inputs(value: object) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except ValueError:
            return {}
    return dict(value or {})  # type: ignore[arg-type]


def main() -> None:
    ap = argparse.ArgumentParser(description="Show a Goal's materialized tool ranking.")
    ap.add_argument("--goal", required=True, help="a Goal uuid or a canonical-name substring")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
