"""Operator commands.

    python -m app.ingestion.admin status                      # queue depth by status, leases, projection lag
    python -m app.ingestion.admin failures [--limit 20]       # last errors of permanently failed jobs
    python -m app.ingestion.admin retry [--job-types T] [--ids 1,2]   # permanent_failed/retryable -> pending
    python -m app.ingestion.admin register-shard K002 --dsn-env K002_DATABASE_URL [--weight 100]
    python -m app.ingestion.admin shard-status K002 full|readonly|active|unhealthy
    python -m app.ingestion.admin shards
    python -m app.ingestion.admin reindex [goal|claim|procedure|all] [--shard K002]
    python -m app.ingestion.admin drain-projections
    python -m app.ingestion.admin verify-projections          # exit 1 if projections disagree with canonical rows
    python -m app.ingestion.admin verify-dedup                # duplicate goal names / procedures without a goal link
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.ingestion import queue as q
from app.ingestion.config import control_database_url


def _parse(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.ingestion.admin", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    f = sub.add_parser("failures")
    f.add_argument("--limit", type=int, default=20)
    r = sub.add_parser("retry")
    r.add_argument("--job-types")
    r.add_argument("--ids")
    rs = sub.add_parser("register-shard")
    rs.add_argument("shard_id")
    rs.add_argument("--dsn-env", required=True, help="NAME of the env var holding the DSN (never the DSN)")
    rs.add_argument("--weight", type=int, default=100)
    rs.add_argument("--capacity-rows", type=int)
    ss = sub.add_parser("shard-status")
    ss.add_argument("shard_id")
    ss.add_argument("status", choices=["active", "full", "readonly", "unhealthy", "retired"])
    sub.add_parser("shards")
    ri = sub.add_parser("reindex")
    ri.add_argument("object_type", nargs="?", default="all", choices=["goal", "claim", "procedure", "all"])
    ri.add_argument("--shard")
    sub.add_parser("drain-projections")
    sub.add_parser("verify-projections")
    sub.add_parser("verify-dedup")
    return p.parse_args(argv)


async def _amain(a: argparse.Namespace) -> int:
    from app.db.session import create_pool
    from app.services import search_projection as sp
    from app.services import shards as sh

    pool = await create_pool(control_database_url(), max_size=2)
    try:
        if a.cmd == "status":
            print(json.dumps({"jobs": await q.stats(pool), "projection_lag": await sp.projection_lag(pool)}, default=str, indent=2))
        elif a.cmd == "failures":
            rows = await pool.fetch(
                "SELECT id, job_type, attempts, last_error, source_id FROM ingestion_jobs WHERE status = 'failed' "
                "ORDER BY completed_at DESC NULLS LAST LIMIT $1", a.limit)
            for r in rows:
                print(json.dumps(dict(r), default=str))
        elif a.cmd == "retry":
            n = await q.retry_failed(pool, job_types=a.job_types.split(",") if a.job_types else None,
                                     ids=[int(x) for x in a.ids.split(",")] if a.ids else None)
            print(json.dumps({"requeued": n}))
        elif a.cmd == "register-shard":
            print(await sh.register_shard(pool, a.shard_id, dsn_env=a.dsn_env, weight=a.weight, capacity_rows=a.capacity_rows))
        elif a.cmd == "shard-status":
            await sh.set_shard_status(pool, a.shard_id, a.status)
            print(f"{a.shard_id} -> {a.status} (existing objects keep their shard; only NEW placements change)")
        elif a.cmd == "shards":
            for s in await sh.list_shards(pool):
                print(s)
        elif a.cmd in ("reindex", "drain-projections"):
            pools = sh.ShardPools(pool)
            if a.cmd == "reindex":
                t = None if a.object_type == "all" else a.object_type
                print(json.dumps(await sp.reindex(pool, t, shard=a.shard, pools=pools), default=str, indent=2))
            else:
                print(json.dumps(await sp.drain_outbox(pool, pools=pools)))
        elif a.cmd == "verify-projections":
            rep = await sp.verify_projection(pool)
            print(json.dumps(rep, default=str, indent=2))
            return 0 if rep["ok"] else 1
        elif a.cmd == "verify-dedup":
            dup = await pool.fetch(
                "SELECT normalized_name, scope_type, count(*) AS n FROM goals WHERE t_invalid IS NULL AND status <> 'merged' "
                "GROUP BY 1, 2 HAVING count(*) > 1")
            unlinked = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND achieves_goal_id IS NULL AND is_engineering_fixture = false")
            unjudged = await pool.fetchval("SELECT count(*) FROM identity_decisions WHERE decision = 'judge_unavailable' AND resolved_id IS NULL")
            rep = {"duplicate_goal_names": [dict(r) for r in dup], "live_procedures_without_goal_link": unlinked,
                   "goals_created_while_judge_unavailable": unjudged}
            print(json.dumps(rep, default=str, indent=2))
            return 0 if not dup and not unlinked else 1
        return 0
    finally:
        await pool.close()


def main(argv=None) -> None:
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
