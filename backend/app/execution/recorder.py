"""
MCP hardening B7/B8: ExecutionRecorder -- a thin, explicit facade over
the durable event log (migration 61's `execution_run_events`).

This module does not decide anything and does not drive anything. Every
function here is called from a real, pre-existing transition point in
`durable_run.py` (or `route_decision.py`), passing the SAME `asyncpg`
connection already inside that transition's own transaction -- so an
event row and the state change it describes commit or roll back
together, never one without the other (CLAUDE.md rule 12: durable state
only, no observable "it happened but wasn't recorded" gap).

`conn` may be either an `asyncpg.Connection` (the common case -- callers
already hold one open) or a `Pool` (accepted for callers, like
`persist_route_decision`, that do not already have an open transaction).
"""
from __future__ import annotations

from typing import Any, Optional, Union

import asyncpg

EVENT_TYPES: tuple[str, ...] = (
    # pre-existing (this session's earlier B7/B8 pass)
    "run_created", "run_claimed", "run_paused", "run_finalized",
    "route_decided",
    "node_claimed", "node_succeeded", "node_failed",
    # B8's own named vocabulary ("at minimum") -- additive, migration 70
    "run_started", "procedure_retrieved", "applicability_checked",
    "plan_created", "implementation_bound", "node_started",
    "tool_called", "tool_result", "knowledge_requested",
    "child_run_created", "node_waiting", "child_run_completed",
    "node_resumed", "verification_started", "verification_completed",
    "run_failed",
    # B7's record_artifact() -- not in B8's 18 named types, but B8's own
    # text is "at minimum" -- additive, migration 72.
    "artifact_recorded",
)

_Executor = Union[asyncpg.Connection, asyncpg.Pool]


async def record_event(
    conn: _Executor, *, execution_run_id: str, event_type: str,
    node_order: Optional[int] = None, payload: Optional[dict[str, Any]] = None,
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown execution_run_events.event_type: {event_type!r}")
    await conn.execute(
        "INSERT INTO execution_run_events (execution_run_id, node_order, event_type, payload) "
        "VALUES ($1,$2,$3,$4::jsonb)",
        execution_run_id, node_order, event_type, payload or {},
    )


async def record_run_created(conn: _Executor, execution_run_id: str, **fields: Any) -> None:
    await record_event(conn, execution_run_id=execution_run_id, event_type="run_created", payload=fields)


async def record_run_claimed(conn: _Executor, execution_run_id: str, *, worker_id: str, from_status: str) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="run_claimed",
        payload={"worker_id": worker_id, "from_status": from_status},
    )


async def record_run_paused(conn: _Executor, execution_run_id: str, *, node_order: int, reason: str) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="run_paused",
        node_order=node_order, payload={"reason": reason},
    )


async def record_run_finalized(conn: _Executor, execution_run_id: str, *, status: str, outcome: str) -> None:
    """B8 names `run_failed` and `run_finalized` as two DISTINCT event
    types (not one type with a status field) -- a failed run emits
    `run_failed`, everything else (succeeded/cancelled) emits
    `run_finalized`, matching the spec's own vocabulary literally rather
    than folding both into one type distinguished only by payload."""
    event_type = "run_failed" if status == "failed" else "run_finalized"
    await record_event(
        conn, execution_run_id=execution_run_id, event_type=event_type,
        payload={"status": status, "outcome": outcome},
    )


async def record_child_run(
    conn: _Executor, parent_execution_run_id: str, *,
    parent_node_order: Optional[int], child_run_id: str,
    child_procedure_id: str, child_procedure_version: int,
) -> None:
    """B7's `record_child_run()` -- recorded on the PARENT's own event
    log when a child ProcedureRun is created (B9-B13's recursive child
    retrieval), so the parent's event trail shows exactly which children
    it spawned and when, not just its own node transitions."""
    await record_event(
        conn, execution_run_id=parent_execution_run_id, event_type="child_run_created",
        node_order=parent_node_order,
        payload={
            "child_run_id": child_run_id, "child_procedure_id": child_procedure_id,
            "child_procedure_version": child_procedure_version,
        },
    )


async def record_verification_started(
    conn: _Executor, execution_run_id: str, *, criterion_id: str, method: str,
) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="verification_started",
        payload={"criterion_id": criterion_id, "method": method},
    )


async def record_verification_completed(
    conn: _Executor, execution_run_id: str, *, overall_state: str, criteria_count: int,
) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="verification_completed",
        payload={"overall_state": overall_state, "criteria_count": criteria_count},
    )


async def record_artifact(
    conn: _Executor, execution_run_id: str, *, node_order: Optional[int],
    kind: str, ref: str, sha256: Optional[str] = None, size_bytes: Optional[int] = None,
) -> None:
    """B7's `record_artifact()`. `ref`/`sha256` are a REFERENCE (B8:
    "Large data is stored as artifact references/hashes"), never the
    artifact's own inline content -- the payload here carries a pointer,
    not a copy."""
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="artifact_recorded",
        node_order=node_order, payload={"kind": kind, "ref": ref, "sha256": sha256, "size_bytes": size_bytes},
    )


async def record_tool_called(
    conn: _Executor, execution_run_id: str, *, node_order: Optional[int],
    requested_endpoint: Optional[str] = None, requested_method: Optional[str] = None,
    requested_server_url: Optional[str] = None, requested_tool_name: Optional[str] = None,
    implementation_version: Optional[str] = None,
) -> None:
    """B27: "record what Stealth requested, the concrete endpoint/
    tool/version" -- an externally-hosted Adapter.execute() (adapters.py)
    already builds this exact dict (requested_endpoint/requested_method
    for HttpApiAdapter, requested_server_url/requested_tool_name for
    McpToolAdapter); this is the durable event that carries it, using
    B8's own pre-existing `tool_called` vocabulary entry (real since
    migration 70, never emitted until now)."""
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="tool_called",
        node_order=node_order,
        payload={
            "requested_endpoint": requested_endpoint, "requested_method": requested_method,
            "requested_server_url": requested_server_url, "requested_tool_name": requested_tool_name,
            "implementation_version": implementation_version,
        },
    )


async def record_tool_result(
    conn: _Executor, execution_run_id: str, *, node_order: Optional[int],
    outcome_status: str, failure_class: Optional[str] = None, detail: Optional[str] = None,
) -> None:
    """B27's other half: "returned results... what was independently
    verified versus merely reported". This event's own payload IS the
    merely-reported half (the external provider's self-reported status)
    -- it is deliberately never written to `execution_run_nodes.
    verification_state='verified'` by the caller, since Stealth did not
    itself independently observe the underlying work (B26: "Distinguish
    Stealth-observed execution from provider-reported... evidence")."""
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="tool_result",
        node_order=node_order,
        payload={"outcome_status": outcome_status, "failure_class": failure_class, "detail": detail},
    )


async def record_route_decided(conn: _Executor, execution_run_id: str, *, route_decision_id: str, route: str) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="route_decided",
        payload={"route_decision_id": route_decision_id, "route": route},
    )


async def record_node_claimed(conn: _Executor, execution_run_id: str, *, node_order: int, worker_id: str, attempt: int) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="node_claimed",
        node_order=node_order, payload={"worker_id": worker_id, "attempt": attempt},
    )


async def record_node_succeeded(conn: _Executor, execution_run_id: str, *, node_order: int) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="node_succeeded", node_order=node_order,
    )


async def record_node_failed(conn: _Executor, execution_run_id: str, *, node_order: int, error_class: Optional[str]) -> None:
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="node_failed",
        node_order=node_order, payload={"error_class": error_class},
    )


async def get_run_events(pool: asyncpg.Pool, execution_run_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, execution_run_id, node_order, event_type, payload, created_at "
        "FROM execution_run_events WHERE execution_run_id = $1 ORDER BY seq",
        execution_run_id,
    )
    return [dict(r) for r in rows]
