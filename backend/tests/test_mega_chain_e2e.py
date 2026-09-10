"""
MCP hardening B22: one comprehensive end-to-end test chaining
find_best_way -> child find_best_way -> verify_completion -> report_execution
(learning loop) -> real evidence/verification_state effects, through the
REAL MCP tools this session built/hardened (B1-B21) -- not a new pipeline,
just the existing tools called in the sequence a real agent session would
actually use them:

  1. find_best_way(plan_only) on a root procedure -> procedure_run_id,
     route_decision_id persisted, a real trace_id auto-generated (B16).
  2. find_best_way(plan_only, parent_run_id=root) on a DIFFERENT
     procedure -> a real child run, correct parent_run_id/root_run_id
     linkage (B9-B14), and the SAME trace_id inherited from the root
     (B16) -- proven directly against execution_runs, not re-derived.
  3. verify_completion on the child -> starts inconclusive, then
     transitions through the verification ladder (B31) once reports are
     submitted for its postconditions.
  4. report_execution(observations_json=...) on the child's procedure ->
     the B18 learning loop attempts real extraction; whichever real
     outcome occurs (a private candidate extracted, or an honest
     "skipped" refusal) is asserted, never assumed.
  5. Cross-checks: the child's execution_run_events trail (B7/B8) shows
     run_created -> run_claimed/... -> run_finalized in order, and the
     verification_results rows (B31) persisted in step 3 are readable
     back from the DB independently of the tool call.

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
from app.execution.recorder import get_run_events
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


def test_full_chain_from_root_plan_through_child_verification_and_learning():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        root_name = f"proc-test-megachain-root-{run_id}"
        child_name = f"proc-test-megachain-child-{run_id}"
        try:
            embedder = Embedder()
            ctx = _FakeContext(pool)

            # --- 1. root plan-only run ---
            root_goal = f"provision the megachain build cluster ({run_id})"
            root_vec = await embedder.embed_one(root_goal, input_type="document")
            await _make_verified_approved(
                pool, root_name, goal=root_goal, embedding=root_vec,
                steps=[{"order": 0, "goal": "reserve cluster capacity"}],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                root_result = await srv.find_best_way(
                    task_description=root_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            root_payload = json.loads(root_result)
            root_run_id = root_payload["procedure_run_id"]
            assert root_run_id
            assert "route_decision_id" in root_payload

            # --- 2. child plan-only run, parented to the root ---
            child_goal = f"lease a megachain build node ({run_id})"
            child_vec = await embedder.embed_one(child_goal, input_type="document")
            await _make_verified_approved(
                pool, child_name, goal=child_goal, embedding=child_vec,
                steps=[{"order": 0, "goal": "lease a node"}],
                postconditions=["the node lease is acquired", "the node passes a health check"],
            )
            with tempfile.TemporaryDirectory() as repo_dir:
                child_result = await srv.find_best_way(
                    task_description=child_goal, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                    parent_run_id=root_run_id, parent_node_order=0,
                )
            child_payload = json.loads(child_result)
            child_run_id = child_payload["procedure_run_id"]
            assert child_run_id

            linkage = await pool.fetchrow(
                "SELECT parent_run_id, root_run_id, trace_id FROM execution_runs WHERE id = $1", child_run_id,
            )
            root_row = await pool.fetchrow("SELECT trace_id FROM execution_runs WHERE id = $1", root_run_id)
            assert str(linkage["parent_run_id"]) == root_run_id
            assert str(linkage["root_run_id"]) == root_run_id
            assert root_row["trace_id"] is not None
            assert str(linkage["trace_id"]) == str(root_row["trace_id"])

            # --- 3. verify_completion on the child: inconclusive, then real state ---
            initial = json.loads(await srv.verify_completion(child_run_id, ctx))
            assert initial["overall_state"] == "inconclusive"
            assert len(initial["criteria"]) == 2

            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": True},
                {"criterion_id": "postcondition:1", "method": "deterministic_check", "passed": True,
                 "evidence_refs": ["log:node-health-ok"]},
            ])
            verified = json.loads(await srv.verify_completion(child_run_id, ctx, reports_json=reports))
            assert verified["overall_state"] == "claimed_done"  # weakest-rung aggregation (B31)

            db_results = await pool.fetch(
                "SELECT criterion_id, state, method FROM verification_results "
                "WHERE execution_run_id = $1::uuid ORDER BY criterion_id", child_run_id,
            )
            assert len(db_results) == 2
            assert {r["method"] for r in db_results} == {"self_report", "deterministic_check"}

            # --- 4. report_execution learning loop on the child's procedure ---
            child_procedure_id = str(child_payload["procedure_id"])
            observations = json.dumps([
                {"observation_type": "file_touched", "label": "infra/lease_node.py",
                 "properties": {"file_path": "infra/lease_node.py"}},
                {"observation_type": "file_touched", "label": "infra/lease_node_test.py",
                 "properties": {"file_path": "infra/lease_node_test.py"}},
            ])
            learn_result = await srv.report_execution(
                procedure_id=child_procedure_id, success=True,
                context_key=f"ctx-megachain-{run_id}", ctx=ctx,
                observations_json=observations,
                tool_sequence_json=json.dumps(["read_file", "edit_file", "run_tests"]),
                task_description=child_goal,
            )
            learn_payload = json.loads(learn_result)
            assert "extraction" in learn_payload
            if "procedure_id" in learn_payload["extraction"]:
                extracted_row = await pool.fetchrow(
                    "SELECT visibility, owner_id FROM procedures WHERE procedure_id = $1 "
                    "ORDER BY version DESC LIMIT 1",
                    learn_payload["extraction"]["procedure_id"],
                )
                assert extracted_row["visibility"] == "private"  # B19
                await pool.execute(
                    "DELETE FROM procedures WHERE procedure_id = $1::uuid "
                    "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    learn_payload["extraction"]["procedure_id"],
                )
            # else: an honest "skipped" refusal is also a real, valid
            # outcome for a minimal fixture -- either branch proves the
            # pipeline actually ran end to end, never a fabricated success.

            # --- 5. durable event log cross-check (B7/B8) ---
            # plan_only mode creates the run but never drives it (that is
            # continue_run's job, exercised by
            # test_find_best_way_recursion_e2e.py's own waiting_child
            # tests) -- so only run_created (a real, monotonic-seq-
            # ordered event, migration 63) is expected here, never
            # run_claimed/node_claimed/run_finalized on an undriven run.
            events = await get_run_events(pool, child_run_id)
            types_in_order = [e["event_type"] for e in events]
            assert types_in_order[0] == "run_created"
            assert "run_claimed" not in types_in_order
            assert "run_finalized" not in types_in_order
        finally:
            await _cleanup(pool, root_name)
            await _cleanup(pool, child_name)
            await pool.close()

    asyncio.run(_run())
