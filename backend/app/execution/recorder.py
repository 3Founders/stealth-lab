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
    "run_created", "run_claimed", "run_paused", "run_finalized",
    "route_decided",
    "node_claimed", "node_succeeded", "node_failed",
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
    await record_event(
        conn, execution_run_id=execution_run_id, event_type="run_finalized",
        payload={"status": status, "outcome": outcome},
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
