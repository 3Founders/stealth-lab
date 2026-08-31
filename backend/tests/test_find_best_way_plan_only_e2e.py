"""
find_best_way's `mode='plan_only'` (product design: a caller that is
itself an LLM-driven agent host -- Claude Code, Cursor, any MCP-embedded
client -- should get the real compiled plan back as data and execute it
with its OWN LLM, rather than pay for a redundant server-side reasoning
pass). Real, live-database proof: no server-side LLM call is made, a
real execution_plans/task_graphs row is persisted, and the returned plan
is the real, composed step list -- including a composed sub-procedure's
steps fully expanded, proving Phase 10 composition and this mode compose
correctly together.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)
from app.services.embeddings import Embedder

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
    # task_graphs/execution_plans are real, append-only/frozen tables
    # (Band 1.7 -- the engine itself rejects DELETE, confirmed live: "D-to-
    # frozen" is enforced, not just documented). This test is the first in
    # this file to actually compile+persist a plan, so a procedure this
    # test captured can become permanently un-deletable the moment
    # find_best_way(mode='plan_only') persists a plan against it -- best-
    # effort cleanup: delete whatever is NOT referenced by a frozen plan,
    # leave the rest (same "test corpus accumulates real rows over time"
    # reality already true elsewhere in this shared dev DB).
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )


async def _make_verified_approved(pool, name: str, **kwargs) -> dict:
    result = await capture_procedure(
        pool, name=name, goal=name, provenance="system_pending_review",
        scope_type="global", **kwargs,
    )
    row_id = result["id"]
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    return result


def test_plan_only_returns_real_composed_plan_with_zero_llm_calls():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-planonly")

            # execution_plans is frozen (Band 1.7) -- a root procedure this
            # test creates can become permanently un-deletable the moment
            # find_best_way(mode='plan_only') persists a plan against it,
            # while its OWN sub-reference (not itself referenced) gets
            # cleaned up by a later run's best-effort _cleanup. A stale,
            # still-verified leftover root with the SAME goal text would
            # then rank as the top match instead of this run's fresh one,
            # pointing at a sub-procedure that no longer exists. A real
            # per-run UUID suffix on every name/goal makes each run's rows
            # unambiguous regardless of what earlier runs left behind.
            from uuid import uuid4
            run_id = uuid4().hex[:8]

            embedder = Embedder()

            sub_goal = f"run the shared lint pass ({run_id})"
            sub_vec = await embedder.embed_one(sub_goal, input_type="document")
            sub = await capture_procedure(
                pool, name=f"proc-test-planonly-sub-{run_id}", goal=sub_goal,
                steps=[{"order": 0, "goal": "run linter"}, {"order": 1, "goal": "fix lint errors"}],
                provenance="system_pending_review", scope_type="global", embedding=sub_vec,
            )
            sub_row = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", sub["id"])

            goal_text = f"implement a new feature end to end -- plan-only test unique phrase ({run_id})"
            root_vec = await embedder.embed_one(goal_text, input_type="document")
            root = await _make_verified_approved(
                pool, f"proc-test-planonly-root-{run_id}",
                steps=[
                    {"order": 0, "goal": "explore repo"},
                    {"order": 1, "goal": f"run the shared lint pass ({run_id})",
                     "subprocedure_ref": {"procedure_id": str(sub_row["procedure_id"]), "version": sub_row["version"]}},
                    {"order": 2, "goal": "verify"},
                ],
                embedding=root_vec,
            )

            # Prove no LLM completion call happens: any attempt to construct
            # the server's OpenAI client raises immediately.
            def _raise_if_constructed(*args, **kwargs):
                raise AssertionError("plan_only must never construct an LLM client")

            original_openai = srv.OpenAI
            srv.OpenAI = _raise_if_constructed
            try:
                ctx = _FakeContext(pool)
                result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only",
                )
            finally:
                srv.OpenAI = original_openai

            payload = json.loads(result)
            assert payload["mode"] == "plan_only"
            assert payload["procedure_id"] == str(root["procedure_id"])

            goals = [s["goal"] for s in payload["steps"]]
            assert goals == ["explore repo", "run linter", "fix lint errors", "verify"], (
                "the composed sub-procedure's real steps must be spliced in, "
                f"got {goals}"
            )
            assert all(s["step_ref"] is None for s in payload["steps"]), (
                "every reference must be fully resolved -- none should remain in the plan"
            )
            assert "report_execution" in payload["instructions"]

            # Real, persisted plan -- not just a returned string.
            plan_row = await pool.fetchrow(
                "SELECT id FROM execution_plans WHERE id = $1::uuid", payload["execution_plan_id"],
            )
            assert plan_row is not None

            # No Execution row was written -- nothing ran yet.
            exec_row = await pool.fetchrow(
                "SELECT id FROM executions WHERE execution_plan_id = $1::uuid", payload["execution_plan_id"],
            )
            assert exec_row is None
        finally:
            await _cleanup(pool, "proc-test-planonly")
            await pool.close()

    asyncio.run(_run())


def test_plan_only_no_match_returns_honest_message_not_full_run(monkeypatch):
    """A real corpus always ranks SOME survivor first among applicable
    candidates (no absolute-similarity floor) -- so "nothing matched" is
    reliably tested by forcing the real zero-candidates case directly,
    not by hoping no procedure in a shared, accumulating dev DB happens
    to rank first. `_respond_plan_only` must never even be invoked (it
    would attempt an LLM-free but still real compile/persist against a
    nonexistent match) -- and it must NOT fall through to a full sandboxed
    run either, matching 'lookup_only's own contract."""
    import app.services.applicability as applicability_module

    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(applicability_module, "find_applicable_procedures", _empty)

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            ctx = _FakeContext(pool)
            called_plan_only = False

            async def _fail_if_called(*args, **kwargs):
                nonlocal called_plan_only
                called_plan_only = True
                return "should not be called"

            monkeypatch.setattr(srv, "_respond_plan_only", _fail_if_called)

            result = await srv.find_best_way(
                task_description="plan-only no-match probe (real corpus bypassed via monkeypatch)",
                ctx=ctx, mode="plan_only",
            )
            assert "No strong existing match found" in result
            assert "REFUSED" not in result
            assert called_plan_only is False
        finally:
            await pool.close()

    asyncio.run(_run())
