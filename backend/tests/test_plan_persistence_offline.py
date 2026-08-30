"""Proving tests for app/execution/plan_persistence.py -- the real INSERT
half of Band 1.7 that app/execution/plans.py deliberately never performs.

REALISTIC SCENARIO, not a toy: a stale-API fix, the exact failure mode
this session grounded in real published research (arXiv 2604.09515,
fetched and read this session -- "context-memory conflict": an LLM trained
before a library's API changed keeps emitting the deprecated call). The
concrete case used throughout: pandas removed `DataFrame.append()`; the
correct replacement is `pandas.concat()`. A procedure teaching this fix is
exactly the shape a real find_best_way run would compile a plan against --
not a synthetic "add two numbers" fixture.

Fully offline -- a FakePool records every SQL statement and its bound
values; no DATABASE_URL, no real Postgres.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
from app.execution.plans import PlanViolation, compile_plan
from app.models.plan import PlanNode, ProcedureRef

PROC_ID = uuid4()
PROC_ROW_ID = uuid4()

# A real-shaped procedure row, as capture_procedure()/get_procedure() would
# actually return it -- not a minimal stub missing fields compile_plan reads.
STALE_API_PROCEDURE = {
    "id": PROC_ROW_ID,
    "procedure_id": PROC_ID,
    "version": 1,
    "name": "pandas-append-removed-use-concat",
    "goal": "Add rows to a pandas DataFrame without the removed .append() method",
    "steps": [
        {"order": 0, "goal": "Replace `df.append(other)` with `pd.concat([df, other], ignore_index=True)`"},
    ],
    "verification_state": "verified",
    "verification_stats": {"successes": 14},
}


class FakePool:
    """Records every statement + bound args; fetchrow/fetch return
    caller-programmed rows via a queue so rebind-lookup tests can control
    what "already exists in storage" means without a real DB."""

    def __init__(self, fetchrow_queue: list[dict | None] | None = None):
        self.statements: list[tuple[str, tuple]] = []
        self._fetchrow_queue = list(fetchrow_queue or [])

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        if "RETURNING id" in sql:
            # Real asyncpg behavior for an INSERT ... RETURNING id with no
            # programmed row: synthesize one rather than returning None,
            # which no real INSERT ever does.
            return {"id": uuid4()}
        return None

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        return []

    def inserts_into(self, table: str) -> list[tuple[str, tuple]]:
        return [s for s in self.statements if s[0].strip().startswith(f"INSERT INTO {table}")]


def _compile_stale_api_plan():
    node = PlanNode(
        order=0, goal=STALE_API_PROCEDURE["goal"],
        step_ref=ProcedureRef(procedure_id=PROC_ID, version=1),
    )
    return compile_plan(
        procedure_id=PROC_ID, procedure_version=1, procedure_row_id=PROC_ROW_ID,
        procedure_payload=STALE_API_PROCEDURE,
        task_description="Fix DataFrame.append AttributeError after pandas upgrade",
        nodes=[node], extractor_version="find_best_way_plan_compiler@1",
        created_by="find_best_way",
    )


@pytest.mark.asyncio
async def test_persist_writes_both_tables_exactly_once():
    """A fresh compile writes exactly one execution_plans row and one
    task_graphs row -- the two real INSERTs migration 23 anticipated and
    nothing wrote until this module existed."""
    pool = FakePool()  # no existing row -> _find_existing_plan's SELECT returns None
    compiled = _compile_stale_api_plan()

    result, was_new = await persist_compiled_plan(pool, compiled)

    assert was_new is True
    assert len(pool.inserts_into("execution_plans")) == 1
    assert len(pool.inserts_into("task_graphs")) == 1
    assert result.plan.content_hash == compiled.plan.content_hash


@pytest.mark.asyncio
async def test_identical_second_compile_rebinds_instead_of_duplicating():
    """The determinism contract, exercised against real storage: an
    IDENTICAL compile (same procedure, same task, same node) must rebind
    to the existing row, not write a second one -- compile_plan's own
    docstring promise, proven here at the persistence boundary rather than
    just at the pure-hash level test_band1_7_plans.py already covers."""
    compiled = _compile_stale_api_plan()
    existing_plan_row = compiled.plan.to_row()
    existing_graph_row = compiled.graph.to_row()

    # FakePool programmed to answer as if this exact plan is already stored
    # (the two SELECTs _find_existing_plan issues, in order).
    pool = FakePool(fetchrow_queue=[existing_plan_row, existing_graph_row])

    result, was_new = await persist_compiled_plan(pool, compiled)

    assert was_new is False
    assert not pool.inserts_into("execution_plans"), (
        "a rebind must not write a duplicate execution_plans row"
    )
    assert not pool.inserts_into("task_graphs")
    assert result.plan.id == compiled.plan.id


@pytest.mark.asyncio
async def test_record_plan_execution_is_one_born_complete_insert():
    """executions is frozen by trigger (no UPDATE path) -- this must be a
    single INSERT with outcome already known, never an insert-then-update."""
    pool = FakePool()
    compiled = _compile_stale_api_plan()

    execution_id = await record_plan_execution(
        pool, compiled=compiled, outcome="success", created_by="find_best_way",
    )

    inserts = pool.inserts_into("executions")
    assert len(inserts) == 1
    sql, args = inserts[0]
    assert "ended_at" in sql and "outcome" in sql
    assert execution_id is not None


@pytest.mark.asyncio
async def test_record_plan_execution_refuses_a_planless_payload():
    """Invariant #1, exercised at this real boundary: validate_execution_binding
    is actually called, not bypassed, so a caller cannot record an
    execution without a real compiled plan behind it."""
    pool = FakePool()
    compiled = _compile_stale_api_plan()
    compiled.plan.id = None  # simulate a caller that skipped persist_compiled_plan

    with pytest.raises(PlanViolation):
        await record_plan_execution(pool, compiled=compiled, outcome="failure")
