"""
MCP hardening B23/B24 x B4: `continue_run`'s `recommended_implementations`
surfaces the real `procedure_implementations` relation for the
current node, end to end -- submit_implementation links an Implementation
to a Procedure, then a plan_only run's continue_run packet names it.

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
from app.execution import implementation_registry
from app.services.claims import capture_claim
from app.services.embeddings import Embedder
from app.services.procedure_implementation_bindings import activate_binding
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


async def _cleanup(pool, proc_name_prefix: str, impl_name: str) -> None:
    await pool.execute(
        "DELETE FROM procedure_implementations WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name = $1)",
        impl_name,
    )
    await pool.execute("DELETE FROM implementations WHERE name = $1", impl_name)
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{proc_name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{proc_name_prefix}%",
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


def test_continue_run_surfaces_the_active_procedure_implementation_binding():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-continuebinding-{run_id}"
        impl_name = f"impl-test-continuebinding-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"rotate the signing keys ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, proc_name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "generate new key"}],
            )

            ctx = _FakeContext(pool)
            submit_result = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="primary", ctx=ctx,
                name=impl_name, kind="tool", provider="test",
            )
            binding = json.loads(submit_result)["binding"]
            await activate_binding(pool, binding["id"])

            # B1/B32: continue_run's relevant_claim_refs must be a REAL
            # get_relevant_claims() call keyed on the current node's own
            # goal ("generate new key"), not the old placeholder that
            # just echoed required_preconditions back.
            claim_subject = f"project:continuebinding-claim-{run_id}"
            await pool.execute("INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", claim_subject)
            claim_id = await capture_claim(
                pool, statement=f"generate new key requires the HSM to be online (probe {run_id})",
                task_ids=[claim_subject], subject=claim_subject,
                predicate="requires", object="hsm_online", claim_type="fact",
                epistemic_status="observed", created_by="tester", scope_type="global", embedder=embedder,
            )
            assert claim_id is not None

            with tempfile.TemporaryDirectory() as repo_dir:
                plan_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            procedure_run_id = json.loads(plan_result)["procedure_run_id"]

            context = json.loads(await srv.continue_run(procedure_run_id, ctx))
            sources = {r["source"] for r in context["recommended_implementations"]}
            impl_ids = {r["implementation_id"] for r in context["recommended_implementations"]}
            assert "procedure_implementation_binding" in sources
            assert binding["implementation_id"] in impl_ids

            assert context["relevant_claim_refs"], "must retrieve the real claim, not echo preconditions"
            assert any(r["claim_id"] == claim_id for r in context["relevant_claim_refs"])
            assert context["relevant_claim_refs"] != context["required_preconditions"]
        finally:
            await pool.execute(
                "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND properties->>'subject' = $1",
                claim_subject,
            )
            await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", claim_subject)
            await _cleanup(pool, proc_name, impl_name)
            await pool.close()

    asyncio.run(_run())
