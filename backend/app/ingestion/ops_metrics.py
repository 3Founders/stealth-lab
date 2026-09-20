"""Operational metrics for ingestion, read from data that ALREADY exists.

No new metric store: everything here is a read-only aggregation over ``ingestion_jobs``, ``projection_outbox``,
``knowledge_shards`` / ``object_routes``, ``retrieval_decisions``, ``identity_decisions`` and the ``llm_spend``
cost ledger, plus a live probe of each shard database (``pg_database_size``, ``pg_stat_activity``, a ``SELECT 1``
round trip). Latency distributions of spans (retrieval, embeddings, model calls) live in the OTel traces the
services already emit (docs/OBSERVABILITY.md); this module reports what the database alone can prove.

    python -m app.ingestion.admin metrics           # one JSON snapshot
    python -m app.ingestion.admin alerts            # evaluate thresholds (ops_alerts.py), dedupe, notify
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

_RATE_LIMIT_RX = r"429|RESOURCE_EXHAUSTED|RateLimit|rate.?limit"


async def _jobs(pool: Any) -> dict[str, Any]:
    r = await pool.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE status = 'pending')                                              AS queued,
          count(*) FILTER (WHERE status = 'processing' AND lease_until >= now())                  AS running,
          count(*) FILTER (WHERE status = 'processing' AND lease_until <  now())                  AS expired_leases,
          count(*) FILTER (WHERE status = 'retryable_failed')                                     AS retryable_failed,
          count(*) FILTER (WHERE status = 'retryable_failed' AND attempts >= 3)                   AS retryable_failed_repeat,
          count(*) FILTER (WHERE status = 'done')                                                 AS completed,
          count(*) FILTER (WHERE status = 'failed')                                               AS permanent_failed,
          count(*) FILTER (WHERE status = 'cancelled')                                            AS cancelled,
          count(*) FILTER (WHERE status = 'failed' AND completed_at > now() - interval '60 minutes') AS permanent_failed_60m,
          count(*) FILTER (WHERE status = 'done'   AND completed_at > now() - interval '5 minutes')  AS done_5m,
          count(*) FILTER (WHERE status = 'done'   AND completed_at > now() - interval '60 minutes') AS done_60m,
          avg(EXTRACT(EPOCH FROM completed_at - started_at))
              FILTER (WHERE status = 'done' AND completed_at > now() - interval '60 minutes')     AS avg_duration_s,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM completed_at - started_at))
              FILTER (WHERE status = 'done' AND completed_at > now() - interval '60 minutes')     AS p95_duration_s
        FROM ingestion_jobs
        """)
    out = {k: (round(float(v or 0), 2) if k in ("avg_duration_s", "p95_duration_s") else int(v or 0)) for k, v in dict(r).items()}
    out["throughput_per_min"] = round(out["done_5m"] / 5.0, 2)
    return out


async def _providers(pool: Any, primary: str) -> dict[str, Any]:
    errs = await pool.fetchrow(
        f"""
        SELECT
          count(*) FILTER (WHERE last_error ~ 'EmbeddingError')                       AS embedding_failures_15m,
          count(*) FILTER (WHERE last_error ~ 'SemanticJudgmentUnavailable')          AS judge_failures_15m,
          count(*) FILTER (WHERE last_error ~* '{_RATE_LIMIT_RX}')                    AS rate_limited_15m,
          count(*) FILTER (WHERE status IN ('retryable_failed','failed'))             AS failing_15m
        FROM ingestion_jobs
        WHERE last_error IS NOT NULL AND coalesce(completed_at, claimed_at, started_at) > now() - interval '15 minutes'
        """)
    ident = await pool.fetch(
        "SELECT judge_provider, count(*) AS n FROM identity_decisions "
        "WHERE created_at > now() - interval '60 minutes' AND judge_provider IS NOT NULL GROUP BY 1")
    by_provider = {r["judge_provider"]: int(r["n"]) for r in ident}
    total = sum(by_provider.values())
    fallback = sum(n for p, n in by_provider.items() if p != primary)
    unavailable = await pool.fetchval(
        "SELECT count(*) FROM identity_decisions WHERE decision = 'judge_unavailable' AND created_at > now() - interval '60 minutes'")
    spend = await pool.fetch(
        "SELECT provider, operation, count(*) AS calls, coalesce(sum(input_tokens),0) AS tin, coalesce(sum(output_tokens),0) AS tout "
        "FROM llm_spend WHERE scope_key = 'ingestion' AND occurred_at > now() - interval '60 minutes' GROUP BY 1, 2")
    calls: dict[str, int] = {}
    for r in spend:
        calls[f"{r['provider']}:{r['operation']}"] = int(r["calls"])
    return {
        "embedding_failures_15m": int(errs["embedding_failures_15m"]), "judge_failures_15m": int(errs["judge_failures_15m"]),
        "rate_limited_15m": int(errs["rate_limited_15m"]), "failing_jobs_15m": int(errs["failing_15m"]),
        "judge_decisions_60m": total, "judge_by_provider_60m": by_provider, "primary_judge": primary,
        "fallback_rate_60m": round(fallback / total, 4) if total else 0.0, "judge_unavailable_60m": int(unavailable or 0),
        "model_calls_60m": calls,
        "embedding_calls_60m": sum(n for k, n in calls.items() if k.endswith(":embedding")),
        "judge_calls_60m": sum(n for k, n in calls.items() if ":judge:" in k),
    }


async def _retrieval(pool: Any) -> dict[str, Any]:
    r = await pool.fetchrow(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE degraded) AS degraded,
               coalesce(avg(cardinality(goal_ids)), 0) AS avg_goal_candidates,
               coalesce(avg(cardinality(procedure_ids)), 0) AS avg_procedure_candidates,
               coalesce(avg(jsonb_array_length(CASE WHEN jsonb_typeof(detail->'shards') = 'array' THEN detail->'shards' ELSE '[]'::jsonb END)), 0) AS avg_shards_touched,
               count(*) FILTER (WHERE jsonb_typeof(detail->'unavailable_shards') = 'array'
                                AND jsonb_array_length(detail->'unavailable_shards') > 0) AS with_unavailable_shards
        FROM retrieval_decisions WHERE created_at > now() - interval '60 minutes'
        """)
    n = int(r["n"])
    return {"requests_60m": n, "degraded_60m": int(r["degraded"]), "degraded_rate_60m": round(int(r["degraded"]) / n, 4) if n else 0.0,
            "avg_goal_candidates": round(float(r["avg_goal_candidates"]), 2),
            "avg_procedure_candidates": round(float(r["avg_procedure_candidates"]), 2),
            "avg_shards_touched": round(float(r["avg_shards_touched"]), 2),
            "with_unavailable_shards_60m": int(r["with_unavailable_shards"]),
            "latency": "span data: see OTel traces (stealth.retrieval / stealth.embedding)"}


async def _probe_shard(shard_id: str, get_pool: Any, timeout: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        pool = await asyncio.wait_for(get_pool(shard_id), timeout)
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout)
        rtt = (time.perf_counter() - t0) * 1000
        row = await asyncio.wait_for(pool.fetchrow(
            "SELECT pg_database_size(current_database())::bigint AS bytes, "
            "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())::int AS conns, "
            "current_setting('max_connections')::int AS max_conns"), timeout)
        return {"reachable": True, "rtt_ms": round(rtt, 1), "size_bytes": int(row["bytes"]),
                "connections": int(row["conns"]), "max_connections": int(row["max_conns"]),
                "connection_use": round(int(row["conns"]) / max(1, int(row["max_conns"])), 4)}
    except Exception as exc:  # noqa: BLE001 -- an unreachable shard is a metric, not a crash
        return {"reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def _shards(pool: Any, pools: Any, timeout: float) -> list[dict[str, Any]]:
    rows = await pool.fetch("SELECT shard_id, status, weight, dsn_env, capacity_rows FROM knowledge_shards ORDER BY shard_id")
    counts = {r["home_shard_id"]: int(r["n"]) for r in await pool.fetch(
        "SELECT home_shard_id, count(*) AS n FROM object_routes GROUP BY 1")}
    probes = await asyncio.gather(*[_probe_shard(r["shard_id"], pools.get, timeout) for r in rows if r["status"] != "retired"])
    live = [r for r in rows if r["status"] != "retired"]
    out = []
    for r, probe in zip(live, probes):
        out.append({"shard_id": r["shard_id"], "status": r["status"], "weight": r["weight"], "dsn_env": r["dsn_env"],
                    "routed_objects": counts.get(r["shard_id"], 0), **probe})
    return out


async def collect(pool: Any, pools: Any = None, *, probe_timeout_s: float = 10.0) -> dict[str, Any]:
    from app.config import settings
    from app.services import search_projection as sp
    from app.services import shards as sh
    from app.services.ingest_budget import IngestBudget

    pools = pools or sh.ShardPools(pool)
    primary = (settings.semantic_provider_primary or "jev").split(",")[0].strip()
    jobs, providers, retrieval, lag, shard_rows, budget = await asyncio.gather(
        _jobs(pool), _providers(pool, primary), _retrieval(pool), sp.projection_lag(pool),
        _shards(pool, pools, probe_timeout_s), IngestBudget(pool).status(fresh=True))
    tokens = await pool.fetchrow(
        "SELECT coalesce(sum(input_tokens),0) AS tin, coalesce(sum(output_tokens),0) AS tout, count(*) AS calls, "
        "count(*) FILTER (WHERE operation = 'embedding') AS embed_calls "
        "FROM llm_spend WHERE occurred_at > now() - interval '24 hours'")
    return {
        "generated_at": time.time(), "jobs": jobs, "providers": providers, "retrieval": retrieval,
        "projection": lag, "shards": shard_rows,
        "cost": {**budget.as_dict(), "model_calls_24h": int(tokens["calls"]), "embedding_requests_24h": int(tokens["embed_calls"]),
                 "input_tokens_24h": int(tokens["tin"]), "output_tokens_24h": int(tokens["tout"])},
    }

