"""Operator commands.

    python -m app.ingestion.admin status                      # queue depth by status, leases, projection lag
    python -m app.ingestion.admin failures [--limit 20]       # last errors of permanently failed jobs
    python -m app.ingestion.admin retry [--job-types T] [--ids 1,2]   # permanent_failed/retryable -> pending
    python -m app.ingestion.admin register-shard K002 --dsn-env K002_DATABASE_URL [--weight 100]
    python -m app.ingestion.admin shard-status K002 full|readonly|active|unhealthy
    python -m app.ingestion.admin shards [--json]
    python -m app.ingestion.admin shard-weight K000 0        # placement weight only (0 = no NEW public placement); nothing moves
    python -m app.ingestion.admin count-source --source-key K [--goal-name "..."]   # idempotency assertions (shard-aware)
    python -m app.ingestion.admin probe-providers             # live 1-call embedding + judge probe (exit 1 = embedding down / judge down)
    python -m app.ingestion.admin reindex [goal|claim|procedure|all] [--shard K002]
    python -m app.ingestion.admin drain-projections
    python -m app.ingestion.admin verify-projections          # exit 1 if projections disagree with canonical rows
    python -m app.ingestion.admin reconcile-claims             # judge claims created while the judge was down; merge/flag
    python -m app.ingestion.admin reconcile-goals [--all]     # judge unreconciled goals; merge same-goal paraphrases
    python -m app.ingestion.admin verify-refs                 # remote references resolve on their shards (no cross-DB FK)
    python -m app.ingestion.admin verify-dedup                # duplicate goal names / procedures without a goal link
    python -m app.ingestion.admin metrics                     # one JSON snapshot: queue, providers, retrieval, shards, projection, cost
    python -m app.ingestion.admin alerts [--no-notify] [--fail-on critical]   # evaluate thresholds, de-dupe, notify (ops_alerts.py)
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
    sh_ = sub.add_parser("shards")
    sh_.add_argument("--json", action="store_true")
    sw = sub.add_parser("shard-weight")
    sw.add_argument("shard_id")
    sw.add_argument("weight", type=int)
    cs = sub.add_parser("count-source")
    cs.add_argument("--source-key", required=True)
    cs.add_argument("--goal-name")
    sub.add_parser("probe-providers")
    ri = sub.add_parser("reindex")
    ri.add_argument("object_type", nargs="?", default="all", choices=["goal", "claim", "procedure", "all"])
    ri.add_argument("--shard")
    sub.add_parser("drain-projections")
    sub.add_parser("verify-projections")
    sub.add_parser("verify-dedup")
    sub.add_parser("verify-refs")
    fi = sub.add_parser("fold-implementations")
    fi.add_argument("--limit", type=int)
    sub.add_parser("metrics")
    al = sub.add_parser("alerts")
    al.add_argument("--no-notify", action="store_true")
    al.add_argument("--fail-on", choices=["critical", "any", "never"], default="never")
    sub.add_parser("reconcile-claims")
    rg = sub.add_parser("reconcile-goals")
    rg.add_argument("--all", action="store_true", help="ignore the time window (legacy corpus sweep)")
    rg.add_argument("--batch", type=int, default=500)
    return p.parse_args(argv)


async def _record_verify(pool, name: str, ok: bool) -> None:
    """Remember the last result so ``alerts`` keeps firing until the check passes (best effort: pre-migration-100 DBs skip it)."""
    try:
        from app.ingestion.ops_alerts import record_verify

        await record_verify(pool, name, ok)
    except Exception:  # noqa: BLE001 -- bookkeeping never changes a verify command's exit code
        pass


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
            rows = await sh.list_shards(pool)
            if a.json:
                print(json.dumps([{"shard_id": s.shard_id, "status": s.status, "weight": s.weight, "dsn_env": s.dsn_env,
                                   "capacity_rows": s.capacity_rows} for s in rows]))
            else:
                for s in rows:
                    print(s)
        elif a.cmd == "shard-weight":
            n = await pool.execute("UPDATE knowledge_shards SET weight = $2, updated_at = now() WHERE shard_id = $1", a.shard_id, max(0, a.weight))
            if n.endswith(" 0"):
                print(f"ERROR: unknown shard {a.shard_id}", file=sys.stderr)
                return 2
            sh.invalidate_shard_cache()
            print(f"{a.shard_id} weight -> {max(0, a.weight)} (only NEW public placement changes)")
        elif a.cmd == "count-source":
            procs = await sh.fanout_fetchval_sum(
                pool, "SELECT count(*) FROM procedures WHERE source_key = $1 AND t_invalid IS NULL", a.source_key, strict=True)
            goals = None
            if a.goal_name:
                from app.services.goals import normalize_goal_name
                goals = await sh.fanout_fetchval_sum(
                    pool, "SELECT count(*) FROM goals WHERE normalized_name = $1 AND t_invalid IS NULL AND status <> 'merged'",
                    normalize_goal_name(a.goal_name), strict=True)
            jobs = await pool.fetchval("SELECT count(*) FROM ingestion_jobs WHERE idempotency_key = $1", a.source_key)
            print(json.dumps({"live_procedures": procs, "live_goals": goals, "jobs_with_key": jobs}))
        elif a.cmd == "probe-providers":
            from app.ingestion.handlers import Dependencies
            import time as _t
            out: dict = {}
            t0 = _t.perf_counter()
            try:
                vec = await Dependencies.get_embedder(pool).embed_one("stealthlab provider probe", "query")
                out["embedding"] = {"ok": True, "dim": len(vec), "ms": round((_t.perf_counter() - t0) * 1000)}
            except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
                out["embedding"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
            t0 = _t.perf_counter()
            try:
                from app.services.semantic.chain import SemanticJudge
                res = await SemanticJudge.from_settings().judge_identity("goal", "find callers of a function", "locate every call site of a function")
                out["judge"] = {"ok": bool(res.ok), "provider": res.provider, "model": res.model, "fallback": bool(res.fallback_used),
                                "ms": round((_t.perf_counter() - t0) * 1000), "reason": res.reason}
            except Exception as exc:  # noqa: BLE001
                out["judge"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
            print(json.dumps(out))
            return 0 if out["embedding"]["ok"] and out["judge"]["ok"] else 1
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
            await _record_verify(pool, "verify-projections", bool(rep["ok"]))
            return 0 if rep["ok"] else 1
        elif a.cmd == "metrics":
            from app.ingestion.ops_metrics import collect
            print(json.dumps(await collect(pool), default=str, indent=2))
        elif a.cmd == "alerts":
            from app.ingestion import ops_alerts
            from app.ingestion.ops_metrics import collect
            rep = await ops_alerts.run(pool, await collect(pool), notify=not a.no_notify)
            print(json.dumps(rep, default=str, indent=2))
            if a.fail_on == "critical" and rep["critical"]:
                return 1
            return 1 if a.fail_on == "any" and rep["firing"] else 0
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
            await _record_verify(pool, "verify-refs", bool(rep["ok"]))
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
            await _record_verify(pool, "verify-dedup", not dup and not unlinked and not dangling)
            return 0 if not dup and not unlinked and not dangling else 1
        return 0
    finally:
        await pool.close()


def main(argv=None) -> None:
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
