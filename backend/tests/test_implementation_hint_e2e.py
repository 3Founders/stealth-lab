"""P1 (product spec: "Complete implementation abstraction") -- real,
live-database proof that a step's `implementation_hint` survives the real
storage round trip: validated at `steps_to_linear_nodes`/
`expand_procedure_steps`, carried onto a real `PlanNode`, persisted into
a real `task_graphs` row's JSONB `nodes` column, and read back unchanged.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
from __future__ import annotations

import os

import pytest

from app.db.session import create_pool
from app.execution.implementations import ImplementationViolation
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.models.plan import TaskGraph
from app.services.procedures import capture_procedure, get_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )


def test_valid_implementation_hint_survives_real_storage_round_trip():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-implhint")

            procedure = await capture_procedure(
                pool,
                name="proc-test-implhint-root",
                goal="prove implementation_hint round-trips through real storage",
                provenance="system_pending_review",
                scope_type="global",
                steps=[
                    {"order": 0, "goal": "run the linter", "implementation_hint": "deterministic"},
                    {"order": 1, "goal": "review the diff", "implementation_hint": ["slm", "frontier"]},
                    {"order": 2, "goal": "no hint at all"},
                ],
            )
            procedure = await get_procedure(pool, procedure["id"])

            nodes = await expand_procedure_steps(
                pool,
                procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"],
                steps=procedure["steps"],
            )
            by_order = {n.order: n for n in nodes}
            assert by_order[0].implementation_hint == ("deterministic",)
            assert by_order[1].implementation_hint == ("slm", "frontier")
            assert by_order[2].implementation_hint is None

            compiled = compile_plan(
                procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"],
                procedure_row_id=procedure["id"],
                procedure_payload=procedure,
                task_description="prove implementation_hint round-trips through real storage",
                nodes=nodes,
                extractor_version="test_implementation_hint_e2e@1",
                created_by="test_implementation_hint_e2e",
            )
            persisted, _ = await persist_compiled_plan(pool, compiled)

            # Real DB round trip: re-fetch the task_graphs row this just
            # wrote and reconstruct it independently of the in-process
            # object above -- proving the JSONB column, not just the
            # Python value, carries the hint.
            row = await pool.fetchrow(
                "SELECT * FROM task_graphs WHERE id = $1", persisted.graph.id,
            )
            reread = TaskGraph.from_row(dict(row))
            reread_by_order = {n.order: n for n in reread.nodes}
            assert reread_by_order[0].implementation_hint == ("deterministic",)
            assert reread_by_order[1].implementation_hint == ("slm", "frontier")
            assert reread_by_order[2].implementation_hint is None
        finally:
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_invalid_implementation_hint_is_rejected_before_any_storage():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-implhint-bad")

            procedure = await capture_procedure(
                pool,
                name="proc-test-implhint-bad-root",
                goal="a step names an unknown implementation kind",
                provenance="system_pending_review",
                scope_type="global",
                steps=[
                    {"order": 0, "goal": "do the work", "implementation_hint": "quantum_hivemind"},
                ],
            )
            procedure = await get_procedure(pool, procedure["id"])

            with pytest.raises(ImplementationViolation):
                await expand_procedure_steps(
                    pool,
                    procedure_id=procedure["procedure_id"],
                    procedure_version=procedure["version"],
                    steps=procedure["steps"],
                )
        finally:
            await pool.close()

    import asyncio
    asyncio.run(_run())
