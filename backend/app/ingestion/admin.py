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
    python -m app.ingestion.admin reconcile-claims             # judge claims created while the judge was down; merge/flag
    python -m app.ingestion.admin reconcile-goals [--all]     # judge unreconciled goals; merge same-goal paraphrases
    python -m app.ingestion.admin verify-refs                 # remote references resolve on their shards (no cross-DB FK)
    python -m app.ingestion.admin verify-dedup                # duplicate goal names / procedures without a goal link
    python -m app.ingestion.admin fold-implementations        # convert archived legacy implementations into step bindings / one-step procedures
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
    sub.add_parser("verify-refs")
    fi = sub.add_parser("fold-implementations")
    fi.add_argument("--limit", type=int)
    sub.add_parser("reconcile-claims")
    rg = sub.add_parser("reconcile-goals")
    rg.add_argument("--all", action="store_true", help="ignore the time window (legacy corpus sweep)")
    rg.add_argument("--batch", type=int, default=500)
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
            import os
            dsn = os.environ.get(a.dsn_env)
            if not dsn:
                print(f"ERROR: env var {a.dsn_env} is not set in this shell; cannot verify the shard database", file=sys.stderr)
                return 2
            problems = await sh.check_shard_schema(dsn)
            if problems:
                print("ERROR: not a usable knowledge shard: " + "; ".join(problems) + " -- provision it with: python scripts/migrate.py --dsn <shard dsn>", file=sys.stderr)
                return 2
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
        elif a.cmd == "reconcile-claims":
            from app.ingestion.handlers import Dependencies
            from app.services.claim_identity import reconcile_claims
            print(json.dumps(await reconcile_claims(pool, embedder=Dependencies.get_embedder(pool), judge=Dependencies.get_judge())))
        elif a.cmd == "reconcile-goals":
            from app.ingestion.handlers import Dependencies
            from app.services.identity_resolution import reconcile_goals
            print(json.dumps(await reconcile_goals(
                pool, judge=Dependencies.get_judge(),
                window_minutes=None if a.all else 30.0, batch=a.batch)))
        elif a.cmd == "fold-implementations":
            from app.ingestion.handlers import Dependencies
            from app.services.fold_implementations import fold_all
            rep = await fold_all(pool, embedder=Dependencies.get_embedder(pool), judge=Dependencies.get_judge(), limit=a.limit)
            print(json.dumps(rep, default=str, indent=2))
            return 0 if not rep["failed"] else 1
        elif a.cmd == "verify-refs":
            rep = await sh.verify_routes(pool)
            print(json.dumps(rep, default=str, indent=2))
            return 0 if rep["ok"] else 1
        elif a.cmd == "verify-dedup":
            names = await sh.fanout_fetch(
                pool, "SELECT normalized_name, scope_type, scope_entity_id FROM goals WHERE t_invalid IS NULL AND status <> 'merged'", strict=True)
            counts: dict[tuple, int] = {}
            for r in names:
                k = (r["normalized_name"], r["scope_type"], r["scope_entity_id"])
                counts[k] = counts.get(k, 0) + 1
            dup = [{"normalized_name": k[0], "scope_type": k[1], "n": n} for k, n in counts.items() if n > 1]
            unlinked = await sh.fanout_fetchval_sum(
                pool, "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND achieves_goal_id IS NULL AND is_engineering_fixture = false", strict=True)
            merged_ids = [str(r["id"]) for r in await sh.fanout_fetch(pool, "SELECT id FROM goals WHERE status = 'merged'", strict=True)]
            dangling = await sh.fanout_fetchval_sum(
                pool, "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND achieves_goal_id = ANY($1::uuid[])", merged_ids, strict=True) if merged_ids else 0
            unjudged = await pool.fetchval("SELECT count(*) FROM identity_decisions WHERE decision = 'judge_unavailable' AND resolved_id IS NULL")
            rep = {"duplicate_goal_names": dup, "live_procedures_without_goal_link": unlinked,
                   "live_procedures_linked_to_merged_goals": dangling, "goals_created_while_judge_unavailable": unjudged}
            print(json.dumps(rep, default=str, indent=2))
            return 0 if not dup and not unlinked and not dangling else 1
        return 0
    finally:
        await pool.close()


def main(argv=None) -> None:
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
