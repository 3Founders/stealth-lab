"""Run row bookkeeping, shared by the run and proposal endpoints."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.models.plan import Plan
from app.models.run import RunResult, RunSummary, StageResult


async def create_run(pool, request_text: str, inputs: dict, plan: Plan, status: str) -> UUID:
    row = await pool.fetchrow(
        """
        INSERT INTO runs (request_text, inputs, plan, status)
        VALUES ($1,$2,$3,$4) RETURNING id
        """,
        request_text,
        inputs,
        plan.model_dump(mode="json"),
        status,
    )
    return row["id"]


async def finish_run(pool, run_id: UUID, result: RunResult) -> None:
    await pool.execute(
        """
        UPDATE runs SET status = $2, outputs = $3, error = $4, finished_at = now()
        WHERE id = $1
        """,
        run_id,
        result.status,
        result.outputs,
        result.error,
    )


async def load_run(pool, run_id: UUID) -> Optional[RunSummary]:
    row = await pool.fetchrow(
        """
        SELECT id, request_text, status, plan, outputs, error, created_at, finished_at
        FROM runs WHERE id = $1
        """,
        run_id,
    )
    if row is None:
        return None

    # Every attempt, not just the winning one: a stage that succeeded on its
    # third implementation is a different fact from one that succeeded first
    # time, and it is the fact worth seeing on the run view.
    traces = await pool.fetch(
        """
        SELECT t.node_ref, t.task_node_id, t.implementation_id, t.attempt, t.outcome,
               t.error, t.cache_hit, t.cost, t.latency_ms, t.timestamp,
               n.name AS task_name, i.name AS implementation_name, i.kind AS implementation_kind
        FROM traces t
        LEFT JOIN task_nodes n ON n.id = t.task_node_id
        LEFT JOIN implementations i ON i.id = t.implementation_id
        WHERE t.run_id = $1
        ORDER BY t.timestamp, t.attempt
        """,
        run_id,
    )

    stages = [
        StageResult(
            node_ref=t["node_ref"] or "",
            task_node_id=t["task_node_id"],
            task_name=t["task_name"] or "",
            implementation_id=t["implementation_id"],
            implementation_name=t["implementation_name"] or "",
            implementation_kind=t["implementation_kind"] or "",
            outcome=t["outcome"],
            attempts=t["attempt"],
            cache_hit=t["cache_hit"],
            cost=float(t["cost"] or 0),
            latency_ms=int(t["latency_ms"] or 0),
            error=t["error"],
        )
        for t in traces
    ]

    return RunSummary(
        id=row["id"],
        request_text=row["request_text"],
        status=row["status"],
        plan=row["plan"] or {},
        outputs=row["outputs"] or {},
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        stages=stages,
        total_cost=sum(s.cost for s in stages),
        total_latency_ms=sum(s.latency_ms for s in stages),
    )


async def list_runs(pool, limit: int = 50) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT r.id, r.request_text, r.status, r.created_at, r.finished_at,
               COALESCE(SUM(t.cost), 0) AS total_cost,
               COALESCE(SUM(t.latency_ms), 0) AS total_latency_ms,
               COUNT(t.id) AS stage_count
        FROM runs r
        LEFT JOIN traces t ON t.run_id = r.id
        GROUP BY r.id
        ORDER BY r.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "request_text": r["request_text"],
            "status": r["status"],
            "created_at": r["created_at"],
            "finished_at": r["finished_at"],
            "total_cost": float(r["total_cost"]),
            "total_latency_ms": int(r["total_latency_ms"]),
            "stage_count": int(r["stage_count"]),
        }
        for r in rows
    ]
