"""
Real per-execution telemetry for Implementations (meta-harness/execu.md
Sec 13/14/32, migration 85). The founder's own instruction on the
disclosed cost-model gap: "don't say we can estimate cause we can't --
after some runs and accumulation of evidence we will, bake this into the
system." This module is that baking-in.

`record_implementation_execution()` is wired as an automatic side effect
of `implementation_executor.py::execute_implementation()` -- the ONE
real chokepoint every dispatch already passes through, so recording
happens for free on every real execution, not as an opt-in extra step a
caller could forget.

`implementation_execution_stats()` is the read side: real, empirical
aggregation over whatever has actually accumulated -- honestly reports
zero samples today for almost every Implementation (confirmed live:
nothing has been recorded yet, since this table is new), and becomes
real as executions happen. No ML model, no guessed number -- Sec 14's
own instruction ("Use empirical statistics and explainable heuristics").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from app.utils.ids import uuid7


async def record_implementation_execution(
    pool: asyncpg.Pool,
    *,
    implementation_id: str,
    executor: str,
    outcome_status: str,
    node_result_data: Optional[dict[str, Any]] = None,
    goal_id: Optional[str] = None,
    execution_run_id: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    """Records ONE real execution attempt. `node_result_data` is the raw
    `NodeResult.data` dict a real provider returned -- field names differ
    per executor kind (DeterministicProvider's own `wall_time_seconds`
    vs FrontierProvider's `prompt_tokens`/`completion_tokens`/
    `llm_calls`), so this function reads whichever real keys are present
    and leaves the rest NULL -- never fabricates a 0 for a quantity a
    given executor kind simply does not produce.
    """
    if outcome_status not in ("success", "failure"):
        raise ValueError(f"outcome_status must be 'success' or 'failure', got {outcome_status!r}")

    data = node_result_data or {}
    wall_seconds = data.get("wall_seconds", data.get("wall_time_seconds"))

    row = await pool.fetchrow(
        """
        INSERT INTO implementation_execution_telemetry (
            id, implementation_id, goal_id, execution_run_id, execution_run_node_id,
            executor, outcome_status, prompt_tokens, completion_tokens, llm_calls,
            wall_seconds, created_by
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8, $9, $10, $11, $12)
        RETURNING *
        """,
        str(uuid7()), implementation_id, goal_id, execution_run_id, execution_run_node_id,
        executor, outcome_status, data.get("prompt_tokens"), data.get("completion_tokens"),
        data.get("llm_calls"), wall_seconds, created_by,
    )
    return dict(row)


@dataclass
class ImplementationExecutionStats:
    implementation_id: str
    sample_count: int
    success_count: int
    success_rate: Optional[float] = None  # None when sample_count == 0 -- never a fabricated 0/1
    mean_wall_seconds: Optional[float] = None
    mean_prompt_tokens: Optional[float] = None
    mean_completion_tokens: Optional[float] = None
    mean_llm_calls: Optional[float] = None


async def implementation_execution_stats(pool: asyncpg.Pool, implementation_id: str) -> ImplementationExecutionStats:
    """Real, empirical aggregation over every recorded execution of this
    Implementation -- a plain mean/rate over real rows, not a model.
    `sample_count=0` (and every derived field `None`) is the honest,
    common-today answer for most Implementations -- confirmed live, this
    table has almost no data yet."""
    row = await pool.fetchrow(
        """
        SELECT
            count(*) AS sample_count,
            count(*) FILTER (WHERE outcome_status = 'success') AS success_count,
            avg(wall_seconds) AS mean_wall_seconds,
            avg(prompt_tokens) AS mean_prompt_tokens,
            avg(completion_tokens) AS mean_completion_tokens,
            avg(llm_calls) AS mean_llm_calls
        FROM implementation_execution_telemetry
        WHERE implementation_id = $1::uuid
        """,
        implementation_id,
    )
    sample_count = int(row["sample_count"] or 0)
    success_count = int(row["success_count"] or 0)
    return ImplementationExecutionStats(
        implementation_id=implementation_id,
        sample_count=sample_count,
        success_count=success_count,
        success_rate=(success_count / sample_count) if sample_count > 0 else None,
        mean_wall_seconds=row["mean_wall_seconds"],
        mean_prompt_tokens=row["mean_prompt_tokens"],
        mean_completion_tokens=row["mean_completion_tokens"],
        mean_llm_calls=row["mean_llm_calls"],
    )
