"""Graduate, watch or retire each tool per goal, with the numbers behind it (docs/goal_router.md P3-04).

    python scripts/tool_lifecycle.py --dry-run               # every goal with evidence
    python scripts/tool_lifecycle.py --goal web --dry-run
    python scripts/tool_lifecycle.py --goal web              # refresh tool_trust first, then the same report

Stages are computed from evidence each time (app/tool_routing/lifecycle.py), so the job has nothing to write except
the trust view's refresh: without --dry-run it refreshes tool_trust (db/192) so the router sees the new stages.
Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from app.tool_routing import lifecycle, trust


async def run(args: argparse.Namespace) -> int:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = args.dsn or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        if not args.dry_run:
            await trust.refresh(conn)
        goals = [args.goal] if args.goal else [r["goal"] for r in await conn.fetch(
            "SELECT DISTINCT goal FROM tool_trust WHERE goal <> '' ORDER BY goal")]
        stages = [s for g in goals for s in await lifecycle.evaluate(conn, g)]
    finally:
        await conn.close()
    if args.json:
        print(json.dumps([s.to_dict() for s in stages], indent=2))
        return 0
    if not stages:
        print("no verified evidence yet")
        return 1
    print(f"{'goal':<14} {'stage':<9} {'card':<44} {'wins/judged':>11} {'lower bd':>8} {'recent fail':>11}  why")
    for s in stages:
        rate = "-" if s.recent_failure_rate is None else f"{s.recent_failure_rate:.0%} of {s.recent_judged}"
        print(f"{s.goal[:14]:<14} {s.stage:<9} {s.card_id[:44]:<44} {str(s.wins) + '/' + str(s.judged):>11} "
              f"{s.lower_bound:>8.3f} {rate:>11}  {s.why}")
    counts = Counter(s.stage for s in stages)
    print("\n" + ", ".join(f"{k} {counts[k]}" for k in ("graduate", "watch", "new", "retiring", "retired") if counts[k])
          + ("  (dry run: nothing refreshed)" if args.dry_run else "  (tool_trust refreshed)"))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Tool graduation and retirement by evidence.")
    ap.add_argument("--goal")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
