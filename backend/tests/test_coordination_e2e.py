"""
MCP hardening B36: live-DB half of multi-agent coordination --
`declare_file_intent`'s conflict/dependency-violation detection,
expired/terminal leases being excluded, and `release_file_intent`.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.coordination import (
    DependencyViolation,
    FileIntentConflict,
    check_file_intent_conflicts,
    declare_file_intent,
    release_file_intent,
)
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.procedures import capture_procedure

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
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _start_run(pool, procedure: dict, steps=None) -> str:
    steps = steps or procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_coordination_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_declare_file_intent_succeeds_with_no_conflict():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-coordok-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure)

            row = await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"src/a-{run_id}.py"],
            )
            assert row["owner_agent_id"] == "agent-a"
            assert row["write_exact"] == [f"src/a-{run_id}.py"]
            assert row["file_intent_lease_expires_at"] is not None
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_renewing_own_declaration_never_conflicts_with_itself():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-coordrenew-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure)

            await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"src/a-{run_id}.py"],
            )
            # Renewing the SAME node's intent, same files -- must not raise.
            row = await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"src/a-{run_id}.py", f"src/b-{run_id}.py"],
            )
            assert set(row["write_exact"]) == {f"src/a-{run_id}.py", f"src/b-{run_id}.py"}
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_declare_file_intent_detects_a_real_cross_run_conflict():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordconfa-{run_id}"
        name_b = f"proc-test-coordconfb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"shared/config-{run_id}.py"],
            )
            with pytest.raises(FileIntentConflict) as exc_info:
                await declare_file_intent(
                    pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                    write_exact=[f"shared/config-{run_id}.py"],
                )
            conflicts = exc_info.value.conflicts
            assert len(conflicts) == 1
            assert conflicts[0].execution_run_id == run_a
            assert conflicts[0].owner_agent_id == "agent-a"
            assert conflicts[0].overlapping_files == [f"shared/config-{run_id}.py"]

            # The conflicting declaration must not have been written for run_b.
            row = await pool.fetchrow(
                "SELECT write_exact FROM execution_run_nodes WHERE execution_run_id = $1::uuid AND node_order = 0",
                run_b,
            )
            assert row["write_exact"] == []
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_declare_file_intent_detects_a_symbol_name_conflict_across_different_files():
    """Two nodes declaring DIFFERENT, non-overlapping write scopes but the
    SAME symbol name must still conflict -- the exact-name-overlap check
    on `symbols_expected_to_modify` runs independently of file-path
    overlap (a shared function/class name across files can mean a shared
    interface both agents are about to change)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordsyma-{run_id}"
        name_b = f"proc-test-coordsymb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"payments_v1_{run_id}.py"],
                symbols_expected_to_modify=[f"process_payment_{run_id}"],
            )
            with pytest.raises(FileIntentConflict) as exc_info:
                await declare_file_intent(
                    pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                    write_exact=[f"payments_v2_{run_id}.py"],  # a DIFFERENT file --
                    symbols_expected_to_modify=[f"process_payment_{run_id}"],  # same symbol
                )
            conflicts = exc_info.value.conflicts
            assert len(conflicts) == 1
            assert conflicts[0].execution_run_id == run_a
            assert conflicts[0].overlapping_files == []  # no file-path overlap at all
            assert conflicts[0].overlapping_symbols == [f"process_payment_{run_id}"]
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_disjoint_symbols_and_disjoint_files_never_conflict():
    """Sanity check in the other direction: neither files nor symbols
    overlap -> no conflict at all, even though both nodes declared
    symbols."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordnosyma-{run_id}"
        name_b = f"proc-test-coordnosymb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"a_{run_id}.py"], symbols_expected_to_modify=[f"foo_{run_id}"],
            )
            # must NOT raise
            result = await declare_file_intent(
                pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                write_exact=[f"b_{run_id}.py"], symbols_expected_to_modify=[f"bar_{run_id}"],
            )
            assert result["owner_agent_id"] == "agent-b"
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_expired_lease_is_excluded_from_conflict_detection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordexpa-{run_id}"
        name_b = f"proc-test-coordexpb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"shared/expired-{run_id}.py"],
            )
            # Manually expire it.
            await pool.execute(
                "UPDATE execution_run_nodes SET file_intent_lease_expires_at = $1 "
                "WHERE execution_run_id = $2::uuid AND node_order = 0",
                datetime.now(timezone.utc) - timedelta(seconds=10), run_a,
            )
            # Must NOT conflict now -- the lease is stale.
            row = await declare_file_intent(
                pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                write_exact=[f"shared/expired-{run_id}.py"],
            )
            assert row["write_exact"] == [f"shared/expired-{run_id}.py"]
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_succeeded_node_is_excluded_from_conflict_detection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordterma-{run_id}"
        name_b = f"proc-test-coordtermb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"shared/done-{run_id}.py"],
            )
            await pool.execute(
                "UPDATE execution_run_nodes SET status = 'succeeded' "
                "WHERE execution_run_id = $1::uuid AND node_order = 0",
                run_a,
            )
            row = await declare_file_intent(
                pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                write_exact=[f"shared/done-{run_id}.py"],
            )
            assert row["write_exact"] == [f"shared/done-{run_id}.py"]
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_dependency_violation_is_detected():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-coorddep-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[
                {"order": 0, "goal": "first"}, {"order": 1, "goal": "second"},
            ])
            exec_run_id = await _start_run(pool, procedure)

            # Node 1 depends on node 0, which has not succeeded yet.
            with pytest.raises(DependencyViolation):
                await declare_file_intent(
                    pool, execution_run_id=exec_run_id, node_order=1, owner_agent_id="agent-a",
                    write_exact=[f"src/second-{run_id}.py"],
                )

            # Mark node 0 succeeded -- node 1's declaration should now work.
            await pool.execute(
                "UPDATE execution_run_nodes SET status = 'succeeded' "
                "WHERE execution_run_id = $1::uuid AND node_order = 0",
                exec_run_id,
            )
            row = await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=1, owner_agent_id="agent-a",
                write_exact=[f"src/second-{run_id}.py"],
            )
            assert row["write_exact"] == [f"src/second-{run_id}.py"]
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_release_file_intent_frees_it_for_future_conflict_checks():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-coordrelease-a-{run_id}"
        name_b = f"proc-test-coordrelease-b-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)

            await declare_file_intent(
                pool, execution_run_id=run_a, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"shared/released-{run_id}.py"],
            )
            await release_file_intent(pool, execution_run_id=run_a, node_order=0)

            row = await declare_file_intent(
                pool, execution_run_id=run_b, node_order=0, owner_agent_id="agent-b",
                write_exact=[f"shared/released-{run_id}.py"],
            )
            assert row["write_exact"] == [f"shared/released-{run_id}.py"]
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())


def test_check_file_intent_conflicts_is_a_pure_read():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-coordreadonly-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure)
            await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=0, owner_agent_id="agent-a",
                write_exact=[f"src/x-{run_id}.py"],
            )
            conflicts = await check_file_intent_conflicts(
                pool, write_exact=[f"src/x-{run_id}.py"], write_globs=[],
            )
            assert len(conflicts) == 1
            row = await pool.fetchrow(
                "SELECT write_exact FROM execution_run_nodes WHERE execution_run_id = $1::uuid AND node_order = 0",
                exec_run_id,
            )
            assert row["write_exact"] == [f"src/x-{run_id}.py"]  # unchanged by the read
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
