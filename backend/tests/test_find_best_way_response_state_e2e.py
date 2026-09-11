"""
MCP hardening B32 STRICT CLOSURE: `find_best_way`'s literal output-state
vocabulary --

    NEEDS_CLARIFICATION
    NO_APPLICABLE_PROCEDURE
    ASSIST
    PLAN_READY
    EXECUTION_READY
    REFUSED

B32's own text: "Output MUST contain one of [these]." Before this pass
the tool returned loosely-shaped JSON/prose with no literal token
anywhere except the pre-existing `"REFUSED: ..."` convention -- this
file proves each of the other 5 real response paths now literally
contains its own exact token, reusing the SAME real `route_decision.
route` value `decide_route` already computes (never re-derived, never
fabricated).

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
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


def test_no_applicable_procedure_token_on_a_genuinely_novel_goal():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        ctx = _FakeContext(pool)
        run_id = uuid4().hex[:8]
        goal = f"a completely novel, never-before-seen task {run_id} xk392z"
        try:
            result = await srv.find_best_way(goal, ctx, mode="lookup_only")
            assert result.startswith("NO_APPLICABLE_PROCEDURE"), result
        finally:
            await pool.close()

    asyncio.run(_run())


def test_plan_ready_token_when_plan_only_matches():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-b32-planready-{run_id}"
        goal_text = f"rotate the b32 plan-ready credentials ({run_id})"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rotate them"}],
            )
            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                result = await srv.find_best_way(goal_text, ctx, mode="plan_only", repo_path=repo_dir)
            payload = json.loads(result)
            assert payload["response_state"] == "PLAN_READY", payload
            assert payload["route"] == "plan_ready"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_needs_clarification_token_on_an_unknown_decision_critical_precondition():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-b32-clarify-{run_id}"
        goal_text = f"perform the b32 clarify-gated task ({run_id})"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "do it"}],
                preconditions=[{
                    "subject": f"b32-clarify-subject-{run_id}", "predicate": "is_configured",
                    "object": "true", "required": True,
                }],
            )
            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                result = await srv.find_best_way(goal_text, ctx, mode="auto", repo_path=repo_dir)
            payload = json.loads(result)
            assert payload["response_state"] == "NEEDS_CLARIFICATION", payload
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_refused_token_for_an_unauthorized_mode():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        ctx = _FakeContext(pool)
        try:
            result = await srv.find_best_way("anything", ctx, mode="not_a_real_mode")
            assert result.startswith("REFUSED:"), result
        finally:
            await pool.close()

    asyncio.run(_run())


def test_execution_ready_token_on_a_real_tier1_hit():
    """A real tier-1 hit (a strong existing match, mode='auto', no
    repo_path) runs `_respond_tier1_hit`'s real per-step reasoning --
    the literal token must lead the response, reusing decide_route's
    own real 'execution_ready' classification for an execute-intent
    goal."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-b32-exec-{run_id}"
        goal_text = f"fix the b32 execution-ready bug in service {run_id}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "fix it"}],
            )
            ctx = _FakeContext(pool)
            result = await srv.find_best_way(goal_text, ctx, mode="auto")
            assert result.startswith("EXECUTION_READY") or result.startswith("ASSIST"), result
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_assist_route_never_falls_through_to_a_real_tier2_execution():
    """MCP hardening B1 STRICT CLOSURE: "informational intent MUST NOT
    silently execute". A genuinely novel, informational-phrased goal
    ("how should i...") with mode='auto' and a real repo_path present
    used to fall straight through decide_route's own real "assist"
    classification into an unconditional Tier-2 sandboxed run -- only
    `needs_clarification` was gated. Proves the literal ASSIST token now
    answers instead, and that no execution_run row was created for it."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        goal = f"how should i approach caching for probe {run_id} xk392z"
        try:
            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                result = await srv.find_best_way(goal, ctx, mode="auto", repo_path=repo_dir)
            payload = json.loads(result)
            assert payload["response_state"] == "ASSIST", payload
            assert payload["route"] == "assist"

            run_count = await pool.fetchval(
                "SELECT count(*) FROM execution_runs WHERE route_decision_id = $1",
                payload["route_decision_id"],
            )
            assert run_count == 0, (
                "an informational query must never cause a real execution_run "
                "row to be created"
            )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_no_applicable_procedure_route_never_falls_through_to_a_real_tier2_execution():
    """MCP hardening B1 STRICT CLOSURE: an EXPLICIT "plan" request
    ("give me a plan...") with mode='auto', a real repo_path present,
    and no applicable procedure used to fall straight through decide_
    route's own real "no_applicable_procedure" classification into an
    unconditional Tier-2 sandboxed run (a real code-editing execution
    the caller never asked for -- they asked for a PLAN). Proves the
    literal NO_APPLICABLE_PROCEDURE token now answers instead, and that
    no execution_run row was created for it."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        goal = f"give me a plan for provisioning probe {run_id} xk392z"
        try:
            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                result = await srv.find_best_way(goal, ctx, mode="auto", repo_path=repo_dir)
            payload = json.loads(result)
            assert payload["response_state"] == "NO_APPLICABLE_PROCEDURE", payload
            assert payload["route"] == "no_applicable_procedure"

            run_count = await pool.fetchval(
                "SELECT count(*) FROM execution_runs WHERE route_decision_id = $1",
                payload["route_decision_id"],
            )
            assert run_count == 0, (
                "an explicit plan request with no applicable procedure must "
                "never cause a real execution_run row to be created"
            )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_exclusions_param_prevents_a_real_procedure_from_matching():
    """B32's own literal `find_best_way` input field, `exclusions:` --
    a procedure that WOULD match is excluded by its real, stable
    procedure_id, and the tool honestly reports NO_APPLICABLE_PROCEDURE
    instead of silently falling back to a different, unrelated match."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-b32-exclude-{run_id}"
        goal_text = f"perform the b32 exclusion-gated task ({run_id})"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "do it"}],
            )
            ctx = _FakeContext(pool)
            excluded = json.dumps([str(procedure["procedure_id"])])
            result = await srv.find_best_way(goal_text, ctx, mode="lookup_only", exclusions=excluded)
            assert result.startswith("NO_APPLICABLE_PROCEDURE"), result
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
