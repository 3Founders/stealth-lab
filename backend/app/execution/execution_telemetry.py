"""
Real per-execution telemetry for bound procedure STEPS (meta-harness/execu.md Sec 13/14/32; table
`step_execution_telemetry`, migration 98 -- formerly keyed by an Implementation id, now by
`(procedure_id, step_order)`, since a step is what carries the binding).

`record_step_execution()` is called from `step_binding.py::execute_node()` -- the one chokepoint every bound
step dispatch passes through -- when the caller supplies `context["procedure_id"]`.

`step_execution_stats()` is the read side: a plain empirical mean/rate over real rows (Sec 14: "use empirical
statistics"). `sample_count=0` (every derived field `None`) is the honest answer for a step that never ran.
No pricing table exists, so `monetary_cost_usd` is never populated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from app.utils.ids import uuid7


async def record_step_execution(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    step_order: int,
    executor: str,
    outcome_status: str,
    node_result_data: Optional[dict[str, Any]] = None,
    goal_id: Optional[str] = None,
    execution_run_id: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    """Records ONE real execution attempt of one bound step. `node_result_data` is the raw `NodeResult.data`;
    field names differ per executor kind, so this reads whichever real keys are present and leaves the rest
    NULL -- never a fabricated 0 for a quantity an executor does not produce."""
    if outcome_status not in ("success", "failure"):
        raise ValueError(f"outcome_status must be 'success' or 'failure', got {outcome_status!r}")
    data = node_result_data or {}
    wall_seconds = data.get("wall_seconds", data.get("wall_time_seconds"))
    row = await pool.fetchrow(
        """
        INSERT INTO step_execution_telemetry (
            id, procedure_id, step_order, goal_id, execution_run_id, execution_run_node_id,
            executor, outcome_status, prompt_tokens, completion_tokens, llm_calls, wall_seconds, created_by
        ) VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13)
        RETURNING *
        """,
        str(uuid7()), procedure_id, step_order, goal_id, execution_run_id, execution_run_node_id,
        executor, outcome_status, data.get("prompt_tokens"), data.get("completion_tokens"),
        data.get("llm_calls"), wall_seconds, created_by,
    )
    return dict(row)


@dataclass
class StepExecutionStats:
    procedure_id: str
    step_order: int
    sample_count: int
    success_count: int
    success_rate: Optional[float] = None  # None when sample_count == 0
    mean_wall_seconds: Optional[float] = None
    mean_prompt_tokens: Optional[float] = None
    mean_completion_tokens: Optional[float] = None
    mean_llm_calls: Optional[float] = None


async def step_execution_stats(pool: asyncpg.Pool, procedure_id: str, step_order: int) -> StepExecutionStats:
    row = await pool.fetchrow(
        """
        SELECT count(*) AS sample_count,
               count(*) FILTER (WHERE outcome_status = 'success') AS success_count,
               avg(wall_seconds) AS mean_wall_seconds, avg(prompt_tokens) AS mean_prompt_tokens,
               avg(completion_tokens) AS mean_completion_tokens, avg(llm_calls) AS mean_llm_calls
        FROM step_execution_telemetry WHERE procedure_id = $1::uuid AND step_order = $2
        """,
        procedure_id, step_order,
    )
    n = int(row["sample_count"] or 0)
    ok = int(row["success_count"] or 0)
    return StepExecutionStats(
        procedure_id=procedure_id, step_order=step_order, sample_count=n, success_count=ok,
        success_rate=(ok / n) if n > 0 else None, mean_wall_seconds=row["mean_wall_seconds"],
        mean_prompt_tokens=row["mean_prompt_tokens"], mean_completion_tokens=row["mean_completion_tokens"],
        mean_llm_calls=row["mean_llm_calls"],
    )
