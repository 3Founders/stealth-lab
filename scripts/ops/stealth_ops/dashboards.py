"""Generate the operational Grafana dashboard (deploy/ops/grafana/stealth-ingestion.json).

No second metric store: every panel is a SQL query over tables that already exist in the control database
(ingestion_jobs, projection_outbox, knowledge_shards/object_routes, retrieval_decisions, identity_decisions, llm_spend).
Point a Grafana PostgreSQL datasource at the control DB with a READ-ONLY role (`GRANT SELECT` on those tables).
Span-derived latencies (goal/procedure retrieval, embeddings, model calls) come from the OTLP traces the services already
emit; per-shard size/connections/latency are live probes in `stealth-ops watch` / `admin metrics` and drive the alerts
(the control database cannot query another shard's pg_stat_*).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import REPO

OUT = REPO / "deploy" / "ops" / "grafana" / "stealth-ingestion.json"
DS = {"type": "grafana-postgresql-datasource", "uid": "${DS}"}

_TS = "$__timeGroup(ts, '5m')"


def _panel(pid: int, title: str, sql: str, *, kind: str = "timeseries", x: int, y: int, w: int = 8, h: int = 7,
           unit: str = "short", fmt: str = "time_series") -> dict[str, Any]:
    return {"id": pid, "type": kind, "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": "A", "datasource": DS, "format": fmt, "rawQuery": True, "editorMode": "code", "rawSql": sql}]}


def _row(pid: int, title: str, y: int) -> dict[str, Any]:
    return {"id": pid, "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def build() -> dict[str, Any]:
    p: list[dict[str, Any]] = []
    n = [0]

    def add(*a: Any, **k: Any) -> None:
        n[0] += 1
        p.append(_panel(n[0] + 100, *a, **k))

    def row(title: str, y: int) -> None:
        n[0] += 1
        p.append(_row(n[0] + 100, title, y))

    row("Ingestion", 0)
    add("Queue by status", "SELECT now() AS time, status AS metric, count(*) AS value FROM ingestion_jobs GROUP BY status",
        kind="bargauge", x=0, y=1, fmt="table")
    add("Completed per 5m (throughput)", f"SELECT {_TS.replace('ts','completed_at')} AS time, count(*) AS done FROM ingestion_jobs "
        "WHERE status='done' AND $__timeFilter(completed_at) GROUP BY 1 ORDER BY 1", x=8, y=1)
    add("Job duration avg / p95 (s)", f"SELECT {_TS.replace('ts','completed_at')} AS time, avg(extract(epoch from completed_at-started_at)) AS avg_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch from completed_at-started_at)) AS p95_s FROM ingestion_jobs "
        "WHERE status='done' AND $__timeFilter(completed_at) GROUP BY 1 ORDER BY 1", x=16, y=1, unit="s")
    add("Failures: retryable / permanent / expired leases", "SELECT now() AS time, "
        "count(*) FILTER (WHERE status='retryable_failed') AS retryable, count(*) FILTER (WHERE status='failed') AS permanent, "
        "count(*) FILTER (WHERE status='processing' AND lease_until < now()) AS expired_leases FROM ingestion_jobs",
        kind="stat", x=0, y=8, fmt="table")
    add("Recent permanent failures", "SELECT id, job_type, attempts, left(last_error, 160) AS last_error, completed_at FROM ingestion_jobs "
        "WHERE status='failed' ORDER BY completed_at DESC NULLS LAST LIMIT 15", kind="table", x=8, y=8, w=16, fmt="table")

    row("Semantic providers", 15)
    add("Model + embedding calls (ingestion) per 5m", f"SELECT {_TS.replace('ts','occurred_at')} AS time, "
        "count(*) FILTER (WHERE operation='embedding') AS embedding_calls, count(*) FILTER (WHERE operation LIKE 'judge:%') AS judge_calls "
        "FROM llm_spend WHERE scope_key='ingestion' AND $__timeFilter(occurred_at) GROUP BY 1 ORDER BY 1", x=0, y=16)
    add("Judge provider mix (fallback rate)", f"SELECT {_TS.replace('ts','created_at')} AS time, coalesce(judge_provider,'none') AS metric, count(*) AS value "
        "FROM identity_decisions WHERE $__timeFilter(created_at) GROUP BY 1,2 ORDER BY 1", x=8, y=16)
    add("Provider failures / 429s (from job errors)", f"SELECT {_TS.replace('ts','coalesce(completed_at, claimed_at)')} AS time, "
        "count(*) FILTER (WHERE last_error ~ 'EmbeddingError') AS embedding, count(*) FILTER (WHERE last_error ~ 'SemanticJudgmentUnavailable') AS judge, "
        "count(*) FILTER (WHERE last_error ~* '429|RESOURCE_EXHAUSTED|rate.?limit') AS rate_limited FROM ingestion_jobs "
        "WHERE last_error IS NOT NULL AND $__timeFilter(coalesce(completed_at, claimed_at)) GROUP BY 1 ORDER BY 1", x=16, y=16)

    row("Retrieval", 23)
    add("Retrievals: total vs degraded", f"SELECT {_TS.replace('ts','created_at')} AS time, count(*) AS total, count(*) FILTER (WHERE degraded) AS degraded "
        "FROM retrieval_decisions WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1", x=0, y=24)
    add("Candidates per retrieval (goals / procedures)", f"SELECT {_TS.replace('ts','created_at')} AS time, avg(cardinality(goal_ids)) AS goals, "
        "avg(cardinality(procedure_ids)) AS procedures FROM retrieval_decisions WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1", x=8, y=24)
    add("Shards touched / unavailable", f"SELECT {_TS.replace('ts','created_at')} AS time, "
        "avg(CASE WHEN jsonb_typeof(detail->'shards')='array' THEN jsonb_array_length(detail->'shards') END) AS shards_touched, "
        "count(*) FILTER (WHERE jsonb_typeof(detail->'unavailable_shards')='array' AND jsonb_array_length(detail->'unavailable_shards')>0) AS with_unavailable "
        "FROM retrieval_decisions WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1", x=16, y=24)

    row("Database / shards", 31)
    add("Shard registry + routed objects", "SELECT k.shard_id, k.status, k.weight, count(r.object_id) AS routed_objects FROM knowledge_shards k "
        "LEFT JOIN object_routes r ON r.home_shard_id = k.shard_id GROUP BY 1,2,3 ORDER BY 1", kind="table", x=0, y=32, fmt="table")
    add("Projection lag", "SELECT now() AS time, count(*) FILTER (WHERE status='pending') AS pending, count(*) FILTER (WHERE status='failed') AS failed, "
        "coalesce(extract(epoch from now()-min(created_at) FILTER (WHERE status='pending')),0) AS oldest_pending_s FROM projection_outbox",
        kind="stat", x=8, y=32, fmt="table")
    add("Control DB size + connections", "SELECT now() AS time, pg_database_size(current_database()) AS bytes, "
        "(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()) AS connections", kind="stat", x=16, y=32, fmt="table")

    row("Cost", 39)
    add("Spend per hour (USD) vs daily cap", "SELECT $__timeGroup(occurred_at, '1h') AS time, sum(estimated_cost) AS usd FROM llm_spend "
        "WHERE $__timeFilter(occurred_at) GROUP BY 1 ORDER BY 1", x=0, y=40, unit="currencyUSD")
    add("Spend last 24h by provider / operation", "SELECT provider, operation, count(*) AS calls, sum(input_tokens) AS tokens_in, sum(output_tokens) AS tokens_out, "
        "round(sum(estimated_cost)::numeric, 4) AS usd FROM llm_spend WHERE occurred_at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY usd DESC",
        kind="table", x=8, y=40, w=16, fmt="table")

    return {
        "title": "Stealth ingestion & retrieval (ops)", "uid": "stealth-ingestion-ops", "schemaVersion": 39, "version": 1, "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"}, "tags": ["stealth", "ingestion"],
        "templating": {"list": [{"name": "DS", "label": "Control DB", "type": "datasource", "query": "grafana-postgresql-datasource"}]},
        "annotations": {"list": []}, "panels": p,
    }


def write(out: Path = OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    return out
