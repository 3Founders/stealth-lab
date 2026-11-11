"""Persist and read comparison results (comparison_results in db/191_task_runs.sql, docs/goal_router.md P2-06).

    python -m scripts.compare.store --goal web --limit 10
    python -m scripts.compare.store --task web-001 --json

save() upserts one row per (task, procedure, card): running the same comparison again updates the row (newer
scores, deltas and run ids), it does not add another. The runs themselves stay in task_runs, one row per attempt.
Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from scripts.compare.contracts import GOALS, ComparisonRecord


async def save(pool: Any, comparisons: Iterable[ComparisonRecord]) -> int:
    n = 0
    for c in comparisons:
        await pool.execute(
            """
            INSERT INTO comparison_results (task_id, goal_id, procedure_id, card_id, n_pairs, scores, pass_rates,
                                            paired_delta, cost_delta_usd, latency_delta_ms, winner, run_ids,
                                            evidence_ref)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (task_id, COALESCE(procedure_id, ''), COALESCE(card_id, '')) DO UPDATE
            SET goal_id = EXCLUDED.goal_id, n_pairs = EXCLUDED.n_pairs, scores = EXCLUDED.scores,
                pass_rates = EXCLUDED.pass_rates, paired_delta = EXCLUDED.paired_delta,
                cost_delta_usd = EXCLUDED.cost_delta_usd, latency_delta_ms = EXCLUDED.latency_delta_ms,
                winner = EXCLUDED.winner, run_ids = EXCLUDED.run_ids, evidence_ref = EXCLUDED.evidence_ref,
                updated_at = now()
            """,
            c.task_id, c.goal_id, c.procedure_id, c.card_id, c.n_pairs, json.dumps(c.scores),
            json.dumps(c.pass_rates), c.paired_delta, c.cost_delta_usd, c.latency_delta_ms, c.winner,
            list(c.run_ids), c.evidence_ref,
        )
        n += 1
    return n


async def query(pool: Any, *, goal: Optional[str] = None, task_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT task_id, goal_id, procedure_id, card_id, n_pairs, scores, pass_rates, paired_delta,
               cost_delta_usd::float8 AS cost_delta_usd, latency_delta_ms, winner, run_ids, evidence_ref,
               created_at, updated_at
        FROM comparison_results
        WHERE ($1::text IS NULL OR goal_id = $1 OR task_id LIKE $3)
          AND ($2::text IS NULL OR task_id = $2)
        ORDER BY updated_at DESC
        LIMIT $4
        """,
        goal, task_id, None if goal is None else _prefix(goal) + "-%", max(1, min(int(limit), 1000)),
    )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("scores", "pass_rates"):
            d[k] = json.loads(d[k]) if isinstance(d[k], str) else dict(d[k] or {})
        out.append(d)
    return out


def _prefix(goal: str) -> str:
    """Task ids carry their goal's prefix (corpus tasks): web-, docs-, code-, pkg-, sec-, db-."""
    return {"code_search": "code", "packages": "pkg", "security": "sec", "database": "db"}.get(goal, goal)


async def main_async(args: argparse.Namespace) -> int:
    from scripts.compare.procedure_runner import dsn

    url = dsn(args.dsn)
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    conn = await asyncpg.connect(url, timeout=30)
    try:
        rows = await query(conn, goal=args.goal, task_id=args.task, limit=args.limit)
    finally:
        await conn.close()
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no comparisons")
        return 1
    print(f"{'task':<10} {'goal':<14} {'pairs':>5} {'proc':>5} {'tool':>5} {'delta':>6} {'cost d $':>9} {'lat d ms':>9} "
          f"{'winner':<10} {'card':<30} updated")
    for r in rows:
        s = r["scores"]
        print(f"{r['task_id']:<10} {(r['goal_id'] or '-')[:14]:<14} {r['n_pairs']:>5} {s.get('procedure', float('nan')):>5.2f} "
              f"{s.get('tool', float('nan')):>5.2f} {r['paired_delta']:>6.2f} {r['cost_delta_usd']:>9.4f} "
              f"{r['latency_delta_ms']:>9.0f} {r['winner']:<10} {(r['card_id'] or '-')[:30]:<30} "
              f"{str(r['updated_at'])[:19]}")
    return 0


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser(description="Show stored comparisons.")
    ap.add_argument("--goal", choices=GOALS)
    ap.add_argument("--task")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
