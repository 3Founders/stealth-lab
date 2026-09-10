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
