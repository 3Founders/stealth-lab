"""
MCP hardening B34/B32: the `verify_completion` MCP tool end to end --
submitting reports for a real procedure_run_id, the overall_state
aggregation, and REFUSED for unknown criterion_id/method/malformed JSON.

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


def test_verify_completion_end_to_end_through_the_real_mcp_tool():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifymcp-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"rebuild the search index safely ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rebuild index"}],
                postconditions=["the index rebuild completes", "no documents are lost"],
            )

            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                plan_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            procedure_run_id = json.loads(plan_result)["procedure_run_id"]

            # Read-only call (no reports) -- both criteria inconclusive.
            initial = json.loads(await srv.verify_completion(procedure_run_id, ctx))
            assert initial["overall_state"] == "inconclusive"
            assert len(initial["criteria"]) == 2
            # B16/B33: a plan_only run never reaches a real terminal
            # state (nothing has driven it) -- procedure_run_complete
            # must be honestly False, never true for an un-terminated run.
            assert initial["procedure_run_complete"] is False
            assert any("terminal state" in m for m in initial["missing_for_completion"])

            # Report a self_report for criterion 0, a deterministic_check
            # for criterion 1 -- overall must be the WEAKEST rung (claimed_done).
            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": True},
                {"criterion_id": "postcondition:1", "method": "deterministic_check", "passed": True,
                 "evidence_refs": ["log:no-docs-missing"]},
            ])
            after = json.loads(await srv.verify_completion(procedure_run_id, ctx, reports_json=reports))
            assert after["overall_state"] == "claimed_done"
            by_id = {c["criterion_id"]: c for c in after["criteria"]}
            assert by_id["postcondition:0"]["state"] == "claimed_done"
            assert by_id["postcondition:1"]["state"] == "verified"

            # Unknown criterion_id -> REFUSED, nothing recorded.
            bad_criterion = await srv.verify_completion(
                procedure_run_id, ctx,
                reports_json=json.dumps([{"criterion_id": "postcondition:99", "method": "self_report", "claimed_success": True}]),
            )
            assert bad_criterion.startswith("REFUSED:")

            # Unknown method -> REFUSED.
            bad_method = await srv.verify_completion(
                procedure_run_id, ctx,
                reports_json=json.dumps([{"criterion_id": "postcondition:0", "method": "vibes", "claimed_success": True}]),
            )
            assert bad_method.startswith("REFUSED:")

            # Malformed JSON -> REFUSED.
            bad_json = await srv.verify_completion(procedure_run_id, ctx, reports_json="not json")
            assert bad_json.startswith("REFUSED:")

            # Nonexistent run -> REFUSED.
            missing_run = await srv.verify_completion(str(uuid4()), ctx)
            assert missing_run.startswith("REFUSED:")
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_procedure_run_complete_requires_terminal_state_and_satisfied_verification():
    """B16/B33: `procedure_run_complete` (verify_completion's own real,
    literal answer to "refusing to mark the Procedure complete until
    required verification is satisfied") is False for a real, terminal,
    UNVERIFIED run, True only once BOTH the run is genuinely terminal AND
    every required criterion is satisfied -- and vacuously True for a
    real terminal run with NO postconditions at all (nothing required
    was never withheld)."""
    async def _run():
        from app.execution.durable_run import execute_run, start_run
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifycomplete-{run_id}"
        name_empty = f"proc-test-verifycompleteempty-{run_id}"
        exec_run_id = None
        exec_run_id2 = None
        try:
            embedder = Embedder()
            goal_text = f"rotate the backup keys safely ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rotate the key"}],
                postconditions=["the key rotation completes"],
            )

            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", procedure["id"])
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), procedure["procedure_id"], pv, procedure["id"], "verifycomplete-e2e",
                    f"pch-{run_id}", f"ch-{run_id}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{run_id}",
                )
            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=procedure["procedure_id"], procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="verifycomplete_e2e",
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="verifycomplete-w1")
            # B16/B33 STRICT CLOSURE: every node succeeding is necessary but
            # NOT sufficient -- this Procedure has a real, required
            # postcondition with no satisfying evidence yet, so the run's
            # own terminal transition (durable_run.py::_finalize's real,
            # fail-closed gate) must stop at 'awaiting_verification', never
            # silently advance to 'succeeded'.
            assert result["status"] == "awaiting_verification", (
                "a run with an unsatisfied required verification criterion "
                "must not become 'succeeded' just because every node did"
            )
            assert "postcondition:0" in result["note"]

            run_row = await pool.fetchrow(
                "SELECT status, final_outcome, final_execution_id FROM execution_runs WHERE id = $1",
                exec_run_id,
            )
            assert run_row["status"] == "awaiting_verification"
            assert run_row["final_outcome"] is None
            assert run_row["final_execution_id"] is None

            ctx = _FakeContext(pool)
            # Terminal-adjacent, but NOT yet verified -- must be honestly incomplete.
            before = json.loads(await srv.verify_completion(exec_run_id, ctx))
            assert before["procedure_run_complete"] is False
            assert any("required criterion" in m for m in before["missing_for_completion"])
            # Still parked at awaiting_verification -- verify_completion's
            # own read-only call (no reports) must not have advanced it.
            still_awaiting = await pool.fetchval(
                "SELECT status FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert still_awaiting == "awaiting_verification"

            # Submit the real required report -- now genuinely complete,
            # and THIS is the only thing that may advance the real
            # terminal transition out of 'awaiting_verification'.
            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": True},
            ])
            after = json.loads(await srv.verify_completion(exec_run_id, ctx, reports_json=reports))
            assert after["procedure_run_complete"] is True
            assert after["missing_for_completion"] == []

            final_row = await pool.fetchrow(
                "SELECT status, final_outcome FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert final_row["status"] == "succeeded", (
                "verify_completion must have performed the real, guarded "
                "awaiting_verification -> succeeded transition once every "
                "required criterion was genuinely satisfied"
            )
            assert final_row["final_outcome"] == "success"

            # A procedure with NO postconditions at all -- vacuously
            # complete the moment it terminates, never blocked on
            # verification that was never required.
            procedure2 = await _make_verified_approved(
                pool, name_empty, goal=f"a procedure with no postconditions ({run_id})",
                steps=[{"order": 0, "goal": "do the one thing"}],
            )
            async with pool.acquire() as c:
                pv2 = await c.fetchval("SELECT version FROM procedures WHERE id=$1", procedure2["id"])
                plan_id2 = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), procedure2["procedure_id"], pv2, procedure2["id"], "verifycomplete-empty-e2e",
                    f"pch2-{run_id}", f"ch2-{run_id}",
                )
                graph_id2 = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id2, f"gh2-{run_id}",
                )
            exec_run_id2 = await start_run(
                pool, execution_plan_id=plan_id2, task_graph_id=graph_id2,
                procedure_id=procedure2["procedure_id"], procedure_version=pv2,
                node_orders=[0], deps={0: []}, created_by="verifycomplete_e2e",
            )
            result2 = await execute_run(pool, exec_run_id2, deps={0: []}, run_node=run_node, worker_id="verifycomplete-w2")
            assert result2["status"] == "succeeded"
            empty_result = json.loads(await srv.verify_completion(exec_run_id2, ctx))
            assert empty_result["criteria"] == []
            assert empty_result["procedure_run_complete"] is True
            assert empty_result["missing_for_completion"] == []
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if exec_run_id2 is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id2)
            await _cleanup(pool, name)
            await _cleanup(pool, name_empty)
            await pool.close()

    asyncio.run(_run())


def test_awaiting_verification_stays_held_with_partial_required_criteria():
    """B16/B33 STRICT CLOSURE negative test: TWO required postconditions,
    only one satisfied -- the run must stay at 'awaiting_verification',
    never advance on a partial set."""
    async def _run():
        from app.execution.durable_run import execute_run, start_run
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifypartial-{run_id}"
        exec_run_id = None
        try:
            embedder = Embedder()
            goal_text = f"rotate two keys safely ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rotate both keys"}],
                postconditions=["the first key rotation completes", "the second key rotation completes"],
            )
            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", procedure["id"])
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), procedure["procedure_id"], pv, procedure["id"], "verifypartial-e2e",
                    f"pch-{run_id}", f"ch-{run_id}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{run_id}",
                )
            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=procedure["procedure_id"], procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="verifypartial_e2e",
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="verifypartial-w1")
            assert result["status"] == "awaiting_verification"

            ctx = _FakeContext(pool)
            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": True},
            ])
            await srv.verify_completion(exec_run_id, ctx, reports_json=reports)
            status_after_one = await pool.fetchval(
                "SELECT status FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert status_after_one == "awaiting_verification", (
                "one of two required criteria satisfied must not be enough to advance"
            )

            reports2 = json.dumps([
                {"criterion_id": "postcondition:1", "method": "self_report", "claimed_success": True},
            ])
            await srv.verify_completion(exec_run_id, ctx, reports_json=reports2)
            status_after_both = await pool.fetchval(
                "SELECT status FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert status_after_both == "succeeded"
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_failed_verification_marks_the_run_failed_not_stuck_forever():
    """B16/B33 STRICT CLOSURE negative test: a required criterion that is
    explicitly reported as FAILED (not merely unreported) must move the
    run to a real 'failed' terminal state -- never stuck at
    'awaiting_verification' forever, and never silently 'succeeded'."""
    async def _run():
        from app.execution.durable_run import execute_run, start_run
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifyfail-{run_id}"
        exec_run_id = None
        try:
            embedder = Embedder()
            goal_text = f"rotate a key that turns out broken ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rotate the key"}],
                postconditions=["the key rotation completes"],
            )
            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", procedure["id"])
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), procedure["procedure_id"], pv, procedure["id"], "verifyfail-e2e",
                    f"pch-{run_id}", f"ch-{run_id}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{run_id}",
                )
            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=procedure["procedure_id"], procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="verifyfail_e2e",
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="verifyfail-w1")
            assert result["status"] == "awaiting_verification"

            ctx = _FakeContext(pool)
            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": False},
            ])
            await srv.verify_completion(exec_run_id, ctx, reports_json=reports)

            final_row = await pool.fetchrow(
                "SELECT status, final_outcome FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert final_row["status"] == "failed", (
                "a definitively failed required criterion must produce a real "
                "'failed' terminal state, never leave the run stuck waiting "
                "for evidence that already arrived and said no"
            )
            assert final_row["final_outcome"] == "failure"
            # The MORE specific reason ('failed_verification', distinct
            # from a real node-execution failure) lives on the real
            # verification_results row + the run_failed event payload,
            # not invented as an illegal final_outcome value.
            vr = await pool.fetchrow(
                "SELECT state FROM verification_results WHERE execution_run_id = $1 "
                "AND criterion_id = 'postcondition:0'", exec_run_id,
            )
            assert vr["state"] == "failed_verification"
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
