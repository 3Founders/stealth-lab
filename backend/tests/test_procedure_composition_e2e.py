"""
Phase 10 (memory-substrate map, product spec): real, live-database proving
tests for `app/execution/procedure_graph.py`'s composition machinery
(`expand_procedure_steps`/`expand_composed_nodes`/`fetch_procedure_version`),
added after the implementation itself landed uncommitted and untested
(a parallel agent was cut off by a rate limit right before writing these).
Same pattern as `test_procedures_e2e.py`: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.execution.procedure_graph import (
    ProcedureCompositionCycle,
    ProcedureCompositionDepthExceeded,
    UnresolvedSubprocedureRef,
    expand_procedure_steps,
    fetch_procedure_version,
)
from app.execution.graph_executor import NodeResult, execute_task_graph
from app.models.plan import TaskGraph
from app.services.procedures import capture_procedure, supersede_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_composition_splices_a_real_subprocedure_with_correct_deps():
    """A references B via a step's subprocedure_ref -- the expanded node
    list must contain BOTH procedures' real steps, contiguously
    renumbered, with A's downstream step correctly depending on B's
    LAST spliced node (not B's reference node, which dissolves)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-compose")

            sub = await capture_procedure(
                pool, name="proc-test-compose-sub", goal="shared lint pass",
                steps=[
                    {"order": 0, "goal": "run linter"},
                    {"order": 1, "goal": "fix lint errors"},
                ],
                provenance="system_pending_review", scope_type="global",
            )
            sub_row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", sub["id"])

            root = await capture_procedure(
                pool, name="proc-test-compose-root", goal="implement feature",
                steps=[
                    {"order": 0, "goal": "explore repo"},
                    {"order": 1, "goal": "run the shared lint pass",
                     "subprocedure_ref": {"procedure_id": str(sub_row["procedure_id"]), "version": sub_row["version"]}},
                    {"order": 2, "goal": "verify"},
                ],
                provenance="system_pending_review", scope_type="global",
            )
            root_row = await pool.fetchrow("SELECT procedure_id, version, steps FROM procedures WHERE id = $1", root["id"])

            expanded = await expand_procedure_steps(
                pool, procedure_id=root_row["procedure_id"], procedure_version=root_row["version"],
                steps=root_row["steps"],
            )

            goals = [n.goal for n in expanded]
            assert goals == ["explore repo", "run linter", "fix lint errors", "verify"], (
                f"expected root's steps with B's real steps spliced in place, got {goals}"
            )
            assert len(expanded) == 4
            assert [n.order for n in expanded] == [0, 1, 2, 3], "orders must be contiguous after splicing"

            # explore_repo -> run linter (B's head)
            assert expanded[1].deps == [expanded[0].order]
            # fix lint errors depends on run linter (B's internal edge, preserved)
            assert expanded[2].deps == [expanded[1].order]
            # verify depends on B's TAIL ("fix lint errors"), not the dissolved reference node
            assert expanded[3].deps == [expanded[2].order]

            # No node in the expanded graph should still carry a step_ref --
            # every reference was fully resolved into real work.
            assert all(n.step_ref is None for n in expanded)
        finally:
            await _cleanup(pool, "proc-test-compose")
            await pool.close()

    asyncio.run(_run())


def test_composition_pins_the_exact_version_not_a_newer_supersession():
    """A step's subprocedure_ref pins an exact version. Superseding that
    procedure to a NEW version must NOT change what an already-pinned
    reference resolves to -- 'version ambiguity' is explicitly forbidden
    by the spec."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-pin")

            sub_v1 = await capture_procedure(
                pool, name="proc-test-pin-sub", goal="v1 goal",
                steps=[{"order": 0, "goal": "v1 step"}],
                provenance="system_pending_review", scope_type="global",
            )
            sub_v1_row = await pool.fetchrow(
                "SELECT procedure_id, version FROM procedures WHERE id = $1", sub_v1["id"]
            )

            # Supersede to v2 with different steps.
            await supersede_procedure(
                pool, prior_row_id=sub_v1["id"],
                changed_fields={"steps": [{"order": 0, "goal": "v2 step -- should NOT be picked up"}]},
            )

            root = await capture_procedure(
                pool, name="proc-test-pin-root", goal="root goal",
                steps=[
                    {"order": 0, "goal": "call sub",
                     "subprocedure_ref": {"procedure_id": str(sub_v1_row["procedure_id"]), "version": sub_v1_row["version"]}},
                ],
                provenance="system_pending_review", scope_type="global",
            )
            root_row = await pool.fetchrow(
                "SELECT procedure_id, version, steps FROM procedures WHERE id = $1", root["id"]
            )

            expanded = await expand_procedure_steps(
                pool, procedure_id=root_row["procedure_id"], procedure_version=root_row["version"],
                steps=root_row["steps"],
            )
            assert [n.goal for n in expanded] == ["v1 step"], (
                "a version-pinned reference must resolve to the PINNED version, "
                "never a newer superseding one"
            )
        finally:
            await _cleanup(pool, "proc-test-pin")
            await pool.close()

    asyncio.run(_run())


def test_composition_refuses_a_cycle_not_infinite_recursion():
    """A references B, B references A -- must raise a clear, named
    exception, never hang or silently truncate."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-cycle")

            a = await capture_procedure(
                pool, name="proc-test-cycle-a", goal="a", steps=[{"order": 0, "goal": "a step"}],
                provenance="system_pending_review", scope_type="global",
            )
            a_row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", a["id"])

            b = await capture_procedure(
                pool, name="proc-test-cycle-b", goal="b",
                steps=[{"order": 0, "goal": "call a",
                        "subprocedure_ref": {"procedure_id": str(a_row["procedure_id"]), "version": a_row["version"]}}],
                provenance="system_pending_review", scope_type="global",
            )
            b_row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", b["id"])

            # Rewrite A's own steps to reference B -- completing the cycle.
            await pool.execute(
                "UPDATE procedures SET steps = $2::jsonb WHERE id = $1",
                a["id"],
                [{"order": 0, "goal": "call b",
                  "subprocedure_ref": {"procedure_id": str(b_row["procedure_id"]), "version": b_row["version"]}}],
            )

            with pytest.raises(ProcedureCompositionCycle):
                await expand_procedure_steps(
                    pool, procedure_id=a_row["procedure_id"], procedure_version=a_row["version"],
                    steps=[{"order": 0, "goal": "call b",
                            "subprocedure_ref": {"procedure_id": str(b_row["procedure_id"]), "version": b_row["version"]}}],
                )
        finally:
            await _cleanup(pool, "proc-test-cycle")
            await pool.close()

    asyncio.run(_run())


def test_composition_depth_cap_is_a_real_independent_net():
    """A chain of DISTINCT (acyclic) procedure versions, longer than
    max_depth, must be refused too -- cycle detection alone is not
    trusted to bound recursion."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-depth")

            # Build a chain proc-0 -> proc-1 -> ... -> proc-4 (5 distinct
            # procedures), then expand with max_depth=2 -- must refuse.
            prior_ref = None
            row_ids = []
            for i in range(5):
                steps = [{"order": 0, "goal": f"step {i}"}]
                if prior_ref is not None:
                    steps.append({"order": 1, "goal": f"call chain {i-1}", "subprocedure_ref": prior_ref})
                result = await capture_procedure(
                    pool, name=f"proc-test-depth-{i}", goal=f"g{i}", steps=steps,
                    provenance="system_pending_review", scope_type="global",
                )
                row_ids.append(result["id"])
                row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", result["id"])
                prior_ref = {"procedure_id": str(row["procedure_id"]), "version": row["version"]}

            root_row = await pool.fetchrow("SELECT procedure_id, version, steps FROM procedures WHERE id = $1", row_ids[-1])

            with pytest.raises(ProcedureCompositionDepthExceeded):
                await expand_procedure_steps(
                    pool, procedure_id=root_row["procedure_id"], procedure_version=root_row["version"],
                    steps=root_row["steps"], max_depth=2,
                )
        finally:
            await _cleanup(pool, "proc-test-depth")
            await pool.close()

    asyncio.run(_run())


def test_composition_refuses_an_unresolved_pinned_reference():
    """A step pins a (procedure_id, version) that doesn't exist -- must
    raise, never silently fall back to another version."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            from uuid import uuid4
            bogus = str(uuid4())
            with pytest.raises(UnresolvedSubprocedureRef):
                await expand_procedure_steps(
                    pool, procedure_id=uuid4(), procedure_version=1,
                    steps=[{"order": 0, "goal": "call missing",
                            "subprocedure_ref": {"procedure_id": bogus, "version": 1}}],
                )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_composed_graph_executes_correctly_end_to_end():
    """A real, expanded composed graph run through execute_task_graph
    (the SAME scheduler find_best_way/LocalAgentRunner use) with a fake
    run_node must visit every real spliced node, in the correct order,
    exactly as if it had been a hand-authored graph of the same shape."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-execgraph")

            sub = await capture_procedure(
                pool, name="proc-test-execgraph-sub", goal="sub",
                steps=[{"order": 0, "goal": "sub step 1"}, {"order": 1, "goal": "sub step 2"}],
                provenance="system_pending_review", scope_type="global",
            )
            sub_row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", sub["id"])

            root_steps = [
                {"order": 0, "goal": "root step 1"},
                {"order": 1, "goal": "call sub",
                 "subprocedure_ref": {"procedure_id": str(sub_row["procedure_id"]), "version": sub_row["version"]}},
                {"order": 2, "goal": "root step 3"},
            ]
            expanded = await expand_procedure_steps(
                pool, procedure_id=sub_row["procedure_id"], procedure_version=999,  # arbitrary root marker, not persisted
                steps=root_steps,
            )

            from uuid import uuid4
            graph = TaskGraph(execution_plan_id=uuid4(), graph_hash="composed-test", nodes=expanded)

            visited: list[str] = []

            async def run_node(node) -> NodeResult:
                visited.append(node.goal)
                return NodeResult(status="success", notes=node.goal)

            result = await execute_task_graph(graph, run_node=run_node)

            assert visited == ["root step 1", "sub step 1", "sub step 2", "root step 3"]
            assert result.outcome == "success"
        finally:
            await _cleanup(pool, "proc-test-execgraph")
            await pool.close()

    asyncio.run(_run())


def test_fetch_procedure_version_returns_none_for_unknown_pair():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            from uuid import uuid4
            row = await fetch_procedure_version(pool, uuid4(), 1)
            assert row is None
        finally:
            await pool.close()

    asyncio.run(_run())
