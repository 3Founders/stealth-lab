"""
MCP hardening B9-B14: `find_best_way(parent_run_id=..., parent_node_order=...)`
dynamic child retrieval, end to end through the real MCP tool -- the
child run's parent_run_id/parent_node_id are set correctly, a cyclic
child selection is REFUSED (not silently allowed), and `continue_run` on
the PARENT reports `waiting_child` while the child is live and clears it
once the child terminates.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM route_decisions WHERE task_description LIKE $1", f"%{name_prefix}%")


async def _make_verified_approved(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    if "embedding" in kwargs and "embedding_model_id" not in kwargs:
        kwargs["embedding_model_id"] = Embedder().embedding_model_id()
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    row_id = result["id"]
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


def test_find_best_way_child_run_carries_correct_parent_linkage():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        parent_name = f"proc-test-recparent-{run_id}"
        child_name = f"proc-test-recchild-{run_id}"
        try:
            embedder = Embedder()
            ctx = _FakeContext(pool)

            parent_goal = f"provision the shared build cluster ({run_id})"
            parent_vec = await embedder.embed_one(parent_goal, input_type="document")
            await _make_verified_approved(
                pool, parent_name, goal=parent_goal, embedding=parent_vec,
                steps=[{"order": 0, "goal": "reserve capacity"}],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                parent_result = await srv.find_best_way(
                    task_description=parent_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            parent_payload = json.loads(parent_result)
            parent_run_id = parent_payload["procedure_run_id"]
            assert parent_run_id

            child_goal = f"acquire a build-node lease ({run_id})"
            child_vec = await embedder.embed_one(child_goal, input_type="document")
            await _make_verified_approved(
                pool, child_name, goal=child_goal, embedding=child_vec,
                steps=[{"order": 0, "goal": "lease a node"}],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                child_result = await srv.find_best_way(
                    task_description=child_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                    parent_run_id=parent_run_id, parent_node_order=0,
                )
            child_payload = json.loads(child_result)
            child_run_id = child_payload["procedure_run_id"]
            assert child_run_id

            row = await pool.fetchrow(
                "SELECT parent_run_id, root_run_id, trace_id FROM execution_runs WHERE id = $1", child_run_id,
            )
            assert str(row["parent_run_id"]) == parent_run_id
            assert str(row["root_run_id"]) == parent_run_id

            # MCP hardening B16: a real trace_id, always populated -- the
            # root call had no session_id, so its trace_id must have been
            # auto-generated (not left NULL); the child must inherit that
            # SAME trace_id (one causal chain), not derive its own from
            # its own (also absent) session_id.
            parent_row = await pool.fetchrow("SELECT trace_id FROM execution_runs WHERE id = $1", parent_run_id)
            assert parent_row["trace_id"] is not None
            assert str(row["trace_id"]) == str(parent_row["trace_id"])

            # B7's record_child_run(): the PARENT's own event log must
            # show a real child_run_created event naming this exact
            # child, not just the DB columns above.
            from app.execution.recorder import get_run_events
            parent_events = await get_run_events(pool, parent_run_id)
            child_events = [e for e in parent_events if e["event_type"] == "child_run_created"]
            assert len(child_events) == 1
            assert child_events[0]["payload"]["child_run_id"] == child_run_id
            assert child_events[0]["node_order"] == 0
        finally:
            await _cleanup(pool, parent_name)
            await _cleanup(pool, child_name)
            await pool.close()

    asyncio.run(_run())


def test_find_best_way_refuses_a_cyclic_child_selection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reccyclefbw-{run_id}"
        try:
            embedder = Embedder()
            ctx = _FakeContext(pool)
            goal_text = f"synchronize the replica shards ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "sync shards"}],
            )

            with tempfile.TemporaryDirectory() as repo_dir:
                root_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            root_payload = json.loads(root_result)
            root_run_id = root_payload["procedure_run_id"]

            # Ask find_best_way for the SAME goal again, as a "child" of
            # the run that already selected this exact procedure --
            # A invoking A is the simplest real cycle.
            with tempfile.TemporaryDirectory() as repo_dir:
                cyclic_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                    parent_run_id=root_run_id, parent_node_order=0,
                )
            assert cyclic_result.startswith("REFUSED:")
            assert "ancestor chain" in cyclic_result or "cyclic" in cyclic_result
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_continue_run_reports_and_clears_waiting_child():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        parent_name = f"proc-test-recwaitfbw-{run_id}"
        child_name = f"proc-test-recwaitchild-{run_id}"
        try:
            embedder = Embedder()
            ctx = _FakeContext(pool)

            parent_goal = f"roll the fleet firmware update ({run_id})"
            parent_vec = await embedder.embed_one(parent_goal, input_type="document")
            await _make_verified_approved(
                pool, parent_name, goal=parent_goal, embedding=parent_vec,
                steps=[{"order": 0, "goal": "stage firmware image"}],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                parent_result = await srv.find_best_way(
                    task_description=parent_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            parent_run_id = json.loads(parent_result)["procedure_run_id"]

            before = json.loads(await srv.continue_run(parent_run_id, ctx))
            assert before["waiting_child"] is None

            child_goal = f"validate the firmware checksum ({run_id})"
            child_vec = await embedder.embed_one(child_goal, input_type="document")
            await _make_verified_approved(
                pool, child_name, goal=child_goal, embedding=child_vec,
                steps=[{"order": 0, "goal": "checksum"}],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                child_result = await srv.find_best_way(
                    task_description=child_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                    parent_run_id=parent_run_id, parent_node_order=0,
                )
            child_run_id = json.loads(child_result)["procedure_run_id"]

            during = json.loads(await srv.continue_run(parent_run_id, ctx))
            assert during["waiting_child"] is not None
            assert during["waiting_child"]["child_run_id"] == child_run_id
            assert during["current_phase_or_node"].endswith(":waiting_child")

            await pool.execute("UPDATE execution_runs SET status = 'cancelled' WHERE id = $1", child_run_id)
            after = json.loads(await srv.continue_run(parent_run_id, ctx))
            assert after["waiting_child"] is None
        finally:
            await _cleanup(pool, parent_name)
            await _cleanup(pool, child_name)
            await pool.close()

    asyncio.run(_run())
