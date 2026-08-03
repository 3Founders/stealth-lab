"""
Trace recording.

Every stage execution writes a row -- always, including failures, including
attempts that were escalated past. Traces are the input to future routing
decisions and the only measurement of what actually works that this system
will ever have, and a trace table that only contains successes cannot answer
the question it exists for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID

log = logging.getLogger(__name__)


@dataclass
class TraceRecord:
    node_ref: str
    outcome: str  # success | failure
    run_id: Optional[UUID] = None
    task_node_id: Optional[UUID] = None
    implementation_id: Optional[UUID] = None
    attempt: int = 0
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    cache_hit: bool = False
    cost: float = 0.0
    latency_ms: int = 0
    parent_trace_id: Optional[UUID] = None


class TraceRecorder(Protocol):
    async def record(self, record: TraceRecord) -> Optional[UUID]: ...


class NullTraceRecorder:
    """For offline execution and tests. Keeps what it was given, in order."""

    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    async def record(self, record: TraceRecord) -> Optional[UUID]:
        self.records.append(record)
        return None


# Bounds on what goes into the trace's jsonb columns. A stage that passes a
# whole extracted grid around would otherwise write megabytes per attempt,
# and the trace table's job is to be queryable, not to be a second copy of
# the data.
MAX_JSON_CHARS = 20_000


def _truncate(payload: dict[str, Any]) -> dict[str, Any]:
    import json

    try:
        encoded = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return {"_unserialisable": True}
    if len(encoded) <= MAX_JSON_CHARS:
        return payload
    return {
        "_truncated": True,
        "_original_chars": len(encoded),
        "keys": sorted(str(k) for k in payload),
    }


class PostgresTraceRecorder:
    def __init__(self, pool):
        self._pool = pool

    async def record(self, record: TraceRecord) -> Optional[UUID]:
        try:
            row = await self._pool.fetchrow(
                """
                INSERT INTO traces (run_id, task_node_id, implementation_id, node_ref,
                                    attempt, input, output, outcome, error, cache_hit,
                                    cost, latency_ms, parent_trace_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                RETURNING id
                """,
                record.run_id,
                record.task_node_id,
                record.implementation_id,
                record.node_ref,
                record.attempt,
                _truncate(record.input),
                _truncate(record.output),
                record.outcome,
                record.error,
                record.cache_hit,
                record.cost,
                record.latency_ms,
                record.parent_trace_id,
            )
            return row["id"] if row else None
        except Exception as exc:  # noqa: BLE001
            # A trace write must never take down the run it is describing.
            # Logged at error because a silently unrecorded stage is a hole in
            # the only measurement there is.
            log.error("failed to write trace for %s: %s", record.node_ref, exc)
            return None
