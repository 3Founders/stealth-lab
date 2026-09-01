"""
Band 1.7 storage half: the two/three real INSERTs that
`app/execution/plans.py` deliberately never performs (that module stays
pure/pool-free so its contracts are provable offline -- see its own
docstring). This module is the boundary that actually writes
`execution_plans` / `task_graphs` / `executions` (db/23_plan_persistence.sql).

Until this file existed, `compile_plan()` had zero real callers outside its
own test -- every matched-procedure run (and, per the `find_best_way`
redesign, every ad-hoc run) computed a `CompiledPlan` and then discarded it.
This is the missing writer, nothing more: no new validation, no new
hashing -- `plans.py` already owns both.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.execution.plans import CompiledPlan, validate_execution_binding
from app.models.plan import Execution, ExecutionPlan, TaskGraph

_PLAN_COLUMNS = (
    # NOTE: no task_graph_id here -- execution_plans has no such column
    # (db/23_plan_persistence.sql confirmed directly). The FK points the
    # OTHER way: task_graphs.execution_plan_id -> execution_plans.id.
    # ExecutionPlan (models/plan.py) carries task_graph_id as an in-memory
    # convenience link; to_row() includes it, but this INSERT must not --
    # a real bug this pass found, live, against real Postgres (offline
    # fakes never had a real column list to be wrong against).
    "id", "procedure_id", "procedure_version", "procedure_row_id",
    "scope_type", "scope_entity_id", "task_description",
    "parameters", "starting_state_id", "resolved_claims", "selected_branches",
    "implementations", "safety_check", "verification_plan",
    "extractor_version", "procedure_content_hash", "content_hash",
    "created_by", "visibility", "owner_id",
)

_GRAPH_COLUMNS = (
    "id", "execution_plan_id", "graph_hash", "nodes",
    "created_by", "visibility", "owner_id",
)

_EXECUTION_COLUMNS = (
    "id", "execution_plan_id", "task_graph_id", "procedure_id",
    "procedure_version", "state_id", "implementation_id", "parameters",
    "trace_id", "started_at", "ended_at", "outcome", "actor_id",
    "created_by", "visibility", "owner_id", "scope_type", "scope_entity_id",
)


async def _row_to_compiled_plan(pool: asyncpg.Pool, plan_row: asyncpg.Record) -> CompiledPlan:
    graph_row = await pool.fetchrow(
        "SELECT * FROM task_graphs WHERE execution_plan_id = $1", plan_row["id"],
    )
    if graph_row is None:
        # Would mean the two INSERTs below ran non-atomically and only the
        # first landed -- defensive, should never happen once this module
        # is the only writer, so surfacing loudly beats silently rebinding
        # a plan with no graph.
        raise RuntimeError(
            f"execution_plans row {plan_row['id']} has no matching task_graphs row"
        )
    # ExecutionPlan.from_row() requires task_graph_id (a real, non-optional
    # field on the model -- the in-memory convenience link), but it is not
    # a real column on execution_plans (see _PLAN_COLUMNS' note above) --
    # inject it from the graph row actually fetched, the only place it's
    # available now that the two tables' real FK direction is respected.
    plan_dict = dict(plan_row)
    plan_dict["task_graph_id"] = graph_row["id"]
    return CompiledPlan(
        plan=ExecutionPlan.from_row(plan_dict),
        graph=TaskGraph.from_row(dict(graph_row)),
    )


async def find_existing_plan(pool: asyncpg.Pool, content_hash: str) -> Optional[CompiledPlan]:
    """Rebind support at the real storage layer: `find_rebindable_plan()`
    (plans.py) is a pure comparison over caller-supplied rows -- this is
    what fetches those rows for real, scoped to the one hash that matters
    (content_hash is globally unique in intent: identical inputs, identical
    hash), so this is a point lookup, not a scan.

    NOT the right lookup for the plan-pinning check ahead of
    `implementation_executor.bind_plan_implementations` (directive Sec
    31) -- binding deliberately changes `content_hash` (see that
    module's own docstring: two compiles of the same procedure/task,
    bound against a registry that has since changed, produce correctly
    DIFFERENT plan content), so a freshly recompiled, not-yet-bound
    plan's content_hash can never equal an already-bound stored plan's.
    `find_plan_for_task`, below, is the stable-identity lookup that
    exists for exactly that check.
    """
    plan_row = await pool.fetchrow(
        "SELECT * FROM execution_plans WHERE content_hash = $1", content_hash,
    )
    if plan_row is None:
        return None
    return await _row_to_compiled_plan(pool, plan_row)


async def find_plan_for_task(
    pool: asyncpg.Pool, *, procedure_row_id: UUID, task_description: str,
) -> Optional[CompiledPlan]:
    """The plan-pinning lookup (directive Sec 31 -- "a newly registered
    implementation must not silently replace an implementation already
    frozen into a plan"): "has THIS exact (procedure_row_id,
    task_description) pair already been compiled, bound, and persisted?",
    keyed on two real, stable, stored columns that binding never touches
    -- unlike `find_existing_plan`'s `content_hash`, which
    `bind_plan_implementations` deliberately changes on every bind (see
    its own docstring), making a content_hash lookup on a freshly
    recompiled, not-yet-bound plan structurally unable to find an
    already-bound one.

    Returns the OLDEST matching plan (`ORDER BY created_at ASC`), so a
    replay always finds the ORIGINAL run's plan, never a later one, even
    if multiple runs somehow raced. `None` when this exact
    (procedure_row_id, task_description) pair has never been compiled
    before -- the real "first run" case, for which the caller resolves
    and freezes a fresh binding instead of reusing one.
    """
    plan_row = await pool.fetchrow(
        "SELECT * FROM execution_plans WHERE procedure_row_id = $1 AND task_description = $2 "
        "ORDER BY created_at ASC LIMIT 1",
        procedure_row_id, task_description,
    )
    if plan_row is None:
        return None
    return await _row_to_compiled_plan(pool, plan_row)


async def persist_compiled_plan(
    pool: asyncpg.Pool, compiled: CompiledPlan,
) -> tuple[CompiledPlan, bool]:
    """Write a compiled plan+graph, or rebind an identical existing one.

    Returns (compiled_plan, was_newly_written). `was_newly_written=False`
    means an earlier compile already produced byte-identical content
    (`compile_plan`'s own determinism contract) and this call rebound to
    it rather than forking a duplicate row -- exactly what
    `find_rebindable_plan` exists for, wired to real storage.

    Two INSERTs, not a transaction: both tables are append-only/frozen by
    trigger (migration 23), so a partial failure between them cannot be
    "corrected" by a rollback in any way that matters -- the defensive
    check in `find_existing_plan` is what catches that state if it ever
    occurs, rather than a transaction pretending to prevent it.
    """
    existing = await find_existing_plan(pool, compiled.plan.content_hash)
    if existing is not None:
        return existing, False

    plan_row = compiled.plan.to_row()
    await pool.execute(
        f"INSERT INTO execution_plans ({', '.join(_PLAN_COLUMNS)}) "
        f"VALUES ({', '.join(f'${i+1}' for i in range(len(_PLAN_COLUMNS)))})",
        *(plan_row[c] for c in _PLAN_COLUMNS),
    )
    graph_row = compiled.graph.to_row()
    await pool.execute(
        f"INSERT INTO task_graphs ({', '.join(_GRAPH_COLUMNS)}) "
        f"VALUES ({', '.join(f'${i+1}' for i in range(len(_GRAPH_COLUMNS)))})",
        *(graph_row[c] for c in _GRAPH_COLUMNS),
    )
    return compiled, True


async def record_plan_execution(
    pool: asyncpg.Pool,
    *,
    compiled: CompiledPlan,
    outcome: str,
    trace_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    parameters: Optional[dict[str, Any]] = None,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    implementation_id: Optional[str] = None,
) -> UUID:
    """Write the one `executions` row for a finished run.

    Born complete, per migration 23's own comment ("writes the trace row
    AFTER the skill settles") -- `executions` rejects UPDATE by trigger, so
    there is no "insert running, update on completion" path here. Call this
    only once the outcome is already known.

    `implementation_id`: the durable implementation identity that actually
    ran, when the caller knows one (e.g.
    `implementation_executor.plan_implementation_id(compiled)` over a plan
    already bound by `bind_plan_implementations` before persist). Defaults
    to `None`, the honest value for a run whose plan never resolved a
    durable implementation -- never fabricated here.
    """
    validate_execution_binding(
        execution_plan_id=compiled.plan.id,
        task_graph_id=compiled.graph.id,
        procedure_id=compiled.plan.procedure.procedure_id,
        procedure_version=compiled.plan.procedure.version,
        plan=compiled.plan,
    )
    now = datetime.now(timezone.utc)
    execution = Execution(
        execution_plan_id=compiled.plan.id,
        task_graph_id=compiled.graph.id,
        procedure=compiled.plan.procedure,
        implementation_id=implementation_id,  # type: ignore[arg-type]
        parameters=parameters or {},
        trace_id=trace_id,
        started_at=started_at or now,
        ended_at=ended_at or now,
        outcome=outcome,  # type: ignore[arg-type]
        actor_id=actor_id,
        created_by=created_by,
        scope_type=scope_type or compiled.plan.scope_type,
        scope_entity_id=scope_entity_id or compiled.plan.scope_entity_id,
    )
    row = execution.to_row()
    result = await pool.fetchrow(
        f"INSERT INTO executions ({', '.join(_EXECUTION_COLUMNS)}) "
        f"VALUES ({', '.join(f'${i+1}' for i in range(len(_EXECUTION_COLUMNS)))}) "
        f"RETURNING id",
        *(row[c] for c in _EXECUTION_COLUMNS),
    )
    return result["id"]
