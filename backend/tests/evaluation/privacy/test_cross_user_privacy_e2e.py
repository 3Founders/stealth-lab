"""
Task spec §12/§13 -- private / cross-user evaluation, two real identities,
against real PostgreSQL and the real REST surface.

Does NOT duplicate what earlier phases already proved:
  - Private Problem invisible to another user, including list/find:
    tests/evaluation/product_model/test_gold_product_model_e2e.py
    (test_private_problem_invisible_to_other_user_visible_to_owner,
    test_private_problem_does_not_leak_through_list_or_find).
  - Cross-user run mutation refusal (resume/retry) via REST + MCP, one
    shared service: tests/evaluation/durable/test_gold_durable_e2e.py
    (test_rest_and_mcp_are_two_skins_over_the_same_durable_resume_service).

This file's own value-add: Benchmark and Evaluation privacy (Phase 2 only
covered Problem), the publish-then-independent-execution flow, and two
real findings about what this codebase's authorization model actually
does and does not promise -- verified against the real code, not assumed
from the task spec's wishlist.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file
in this suite.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from tests.test_product_model_e2e import _make_procedure, _run_executions  # noqa: E402


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def with_pool(fn):
    async def wrapper():
        p = await create_pool(statement_cache_size=0)
        try:
            await fn(p)
        finally:
            await p.close()
    return pytest.mark.asyncio(wrapper)


async def _rest_app(pool):
    from fastapi import FastAPI
    from app.api import problems as problems_api

    app = FastAPI()

    @app.middleware("http")
    async def _attach_pool(request, call_next):
        request.app.state.pool = pool
        return await call_next(request)

    app.include_router(problems_api.router)
    return app


async def _rest_client(pool):
    import httpx
    app = await _rest_app(pool)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


# ---------------------------------------------------------------------------
# CONFIRMED GAP (not fixed here -- house rules against touching product
# code): a private Problem's Benchmarks and Evaluations are NOT actually
# scope-checked, at either the service layer or the REST surface, unlike
# its Solutions (list_problem_solutions correctly gates on get_problem's
# visibility first -- product_model.py:278). Verified directly against the
# real code before writing this test:
#
#   product_model.get_benchmark(pool, benchmark_id)            -- no `scope` param at all
#   product_model.list_problem_benchmarks(pool, problem_id)    -- no `scope` param at all
#   product_model.get_evaluation(pool, evaluation_id)          -- no `scope` param at all
#   product_model.list_problem_evaluations(pool, problem_id)   -- no `scope` param at all
#
# and at the REST layer (app/api/problems.py):
#   GET /v1/problems/{id}/benchmarks   -> pm.list_problem_benchmarks(pool, problem_id)   [no scope threaded]
#   GET /v1/problems/{id}/evaluations  -> pm.list_problem_evaluations(pool, problem_id)  [no scope threaded]
#   GET /v1/benchmarks/{id}            -> pm.get_benchmark(pool, benchmark_id)           [no scope threaded]
#   GET /v1/evaluations/{id}           -> pm.get_evaluation(pool, evaluation_id)         [no scope threaded]
#
# every one of those four routes DOES accept `scope: AccessScope = Depends(get_scope)`
# as a parameter (so a caller-supplied X-Viewer-Id IS resolved) -- it is simply
# never passed into the service call, unlike the sibling
# `GET /v1/problems/{id}/solutions` route on the same file, which does thread
# scope through. This is an inconsistency with no documented rationale
# (contrast app/api/runs.py's module docstring, which explicitly states
# "reads are open" as a deliberate policy for that feature -- no such
# statement exists here for benchmarks/evaluations). This is a real,
# reportable privacy gap: a private Problem's benchmark/evaluation content
# is fully readable by any other resolved identity (or anonymously) if the
# UUID is known, and DISCOVERABLE without even knowing the UUID via the
# list endpoints, which take no problem-visibility gate at all.
# ---------------------------------------------------------------------------
@with_pool
async def test_private_benchmark_and_evaluation_are_not_scope_checked_confirmed_gap(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[privacy-gold {tag}] private benchmark/evaluation leak probe",
        proposer="userA", owner_id="userA", visibility="private",
    )
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="secret-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"privacy-gold-{tag}-proc")
    sol = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id,
        owner_id="userA",
    )
    exec_ids = await _run_executions(pool, proc_id, proc_row, n=5, successes=5)
    ev = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
    )
    await pm.complete_evaluation(pool, ev["id"], execution_ids=exec_ids)

    other_scope = AccessScope.for_user("userB")

    # Sanity: the parent Problem itself IS correctly private (Phase 2's
    # proof, re-confirmed here as the premise this gap contradicts).
    assert await pm.get_problem(pool, problem["id"], scope=other_scope) is None

    # The gap: Benchmark and Evaluation reads take no scope at all, so they
    # succeed for "userB" regardless of the parent Problem's privacy.
    leaked_bench = await pm.get_benchmark(pool, bench["id"])
    assert leaked_bench is not None and leaked_bench["id"] == bench["id"], (
        "confirmed gap: get_benchmark() has no scope parameter to enforce "
        "the parent Problem's privacy with"
    )
    leaked_benches_list = await pm.list_problem_benchmarks(pool, problem["id"])
    assert bench["id"] in {b["id"] for b in leaked_benches_list}, (
        "confirmed gap: list_problem_benchmarks() never gates on Problem "
        "visibility at all, unlike list_problem_solutions()"
    )
    leaked_eval = await pm.get_evaluation(pool, ev["id"])
    assert leaked_eval is not None and leaked_eval["id"] == ev["id"], (
        "confirmed gap: get_evaluation() has no scope parameter"
    )
    leaked_evals_list = await pm.list_problem_evaluations(pool, problem["id"])
    assert ev["id"] in {e["id"] for e in leaked_evals_list}, (
        "confirmed gap: list_problem_evaluations() never gates on Problem visibility"
    )

    # Same gap reachable over the real REST surface, not just the service
    # layer -- as "userB" via the same X-Viewer-Id header the product's own
    # get_scope() dependency trusts.
    client = await _rest_client(pool)
    async with client:
        r_problem = await client.get(f"/v1/problems/{problem['id']}", headers={"X-Viewer-Id": "userB"})
        assert r_problem.status_code == 404, "the Problem itself is correctly gated over REST"

        r_bench_list = await client.get(
            f"/v1/problems/{problem['id']}/benchmarks", headers={"X-Viewer-Id": "userB"},
        )
        assert r_bench_list.status_code == 200
        assert bench["id"] in {b["id"] for b in r_bench_list.json()["benchmarks"]}, (
            "confirmed gap over REST: GET /v1/problems/{id}/benchmarks leaks a private "
            "problem's benchmarks to another viewer"
        )

        r_eval_list = await client.get(
            f"/v1/problems/{problem['id']}/evaluations", headers={"X-Viewer-Id": "userB"},
        )
        assert r_eval_list.status_code == 200
        assert ev["id"] in {e["id"] for e in r_eval_list.json()["evaluations"]}, (
            "confirmed gap over REST: GET /v1/problems/{id}/evaluations leaks a private "
            "problem's evaluations to another viewer"
        )

        r_bench_direct = await client.get(f"/v1/benchmarks/{bench['id']}", headers={"X-Viewer-Id": "userB"})
        assert r_bench_direct.status_code == 200 and r_bench_direct.json()["id"] == bench["id"], (
            "confirmed gap over REST: GET /v1/benchmarks/{id} does not check the "
            "parent Problem's visibility"
        )

        r_eval_direct = await client.get(f"/v1/evaluations/{ev['id']}", headers={"X-Viewer-Id": "userB"})
        assert r_eval_direct.status_code == 200 and r_eval_direct.json()["id"] == ev["id"], (
            "confirmed gap over REST: GET /v1/evaluations/{id} does not check the "
            "parent Problem's visibility"
        )


# ---------------------------------------------------------------------------
# Publish (this codebase's real mechanism: visibility is a create-time
# field, not a later lifecycle transition -- there is no publish_solution/
# publish_problem function anywhere in product_model.py, confirmed by
# search. A Solution "has no independent visibility -- it inherits the
# Problem's" (product_model.py's own docstring on list_problem_solutions),
# so "A publishes a Solution" means: A creates the Problem itself with
# visibility='public' and associates the Solution to it) -> B can discover
# it, execute it independently, and B's own evidence is B's, never counted
# as A's original evaluation.
# ---------------------------------------------------------------------------
@with_pool
async def test_published_solution_discoverable_by_b_and_bs_execution_is_independent_evidence(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[privacy-gold {tag}] deflake the shared test runner",
        objective="stop the shared CI runner from flaking on retries",
        proposer="userA", owner_id="userA", visibility="public",
    )
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="published-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"privacy-gold-{tag}-published-proc")
    sol = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id,
        owner_id="userA",
    )
    a_exec_ids = await _run_executions(pool, proc_id, proc_row, n=10, successes=9)
    a_eval = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
    )
    await pm.complete_evaluation(pool, a_eval["id"], execution_ids=a_exec_ids)

    b_scope = AccessScope.for_user("userB")

    # B discovers ONLY what A explicitly published -- the public Problem,
    # reachable by lexical search on its own real content (not a shared
    # random tag collision, per this suite's own established discipline
    # around this shared, never-cleaned-up dev DB).
    found = await pm.find_problem(pool, f"deflake shared test runner {tag}", scope=b_scope)
    assert problem["id"] in {p["id"] for p in found}
    assert await pm.get_problem(pool, problem["id"], scope=b_scope) is not None
    solutions = await pm.list_problem_solutions(pool, problem["id"], scope=b_scope)
    assert sol["id"] in {s["id"] for s in solutions}

    # B independently executes the same procedure/solution. This is B's
    # own real execution lineage, run separately from A's.
    b_exec_ids = await _run_executions(pool, proc_id, proc_row, n=4, successes=1)
    b_eval = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
    )
    await pm.complete_evaluation(pool, b_eval["id"], execution_ids=b_exec_ids)

    # B's evaluation is its own row, with its own recomputed metrics from
    # ONLY the executions B linked -- never merged into or overriding A's.
    # (complete_evaluation()'s own return is the bare `evaluations` row;
    # get_evaluation() is what joins in the linked execution ids.)
    assert b_eval["id"] != a_eval["id"]
    a_reloaded = await pm.get_evaluation(pool, a_eval["id"])
    b_reloaded = await pm.get_evaluation(pool, b_eval["id"])
    assert set(a_reloaded["executions"]) == set(a_exec_ids), (
        "A's evaluation must still be linked to exactly A's own executions -- "
        "B's independent run must never be folded into it"
    )
    assert set(b_reloaded["executions"]) == set(b_exec_ids)
    assert a_reloaded["run_count"] == 10 and b_reloaded["run_count"] == 4, (
        "each evaluation's run_count is recomputed from its own linked executions only"
    )


# ---------------------------------------------------------------------------
# Documented-by-design (NOT a gap -- verified against app/api/runs.py's own
# module docstring before writing this note): "User B cannot inspect A's
# private run" does not hold for durable execution runs in this codebase.
# runs.py states explicitly: "Authorization (§2/§9): reads are open
# (AccessScope.unrestricted() posture, same as every other reader).
# Mutations are gated..." -- GET /v1/runs/{id} and MCP inspect_run take no
# viewer/ownership check at all, by deliberate stated policy; only
# resume/retry (mutations) are gated on created_by, which
# tests/evaluation/durable/test_gold_durable_e2e.py::
# test_rest_and_mcp_are_two_skins_over_the_same_durable_resume_service
# already proves thoroughly (cross-user resume refusal via both REST 403
# and MCP "REFUSED: not your run", plus terminal idempotency and
# successful-node-retry refusal). Not re-tested here to avoid a pointless
# duplicate; this comment exists so the discrepancy between the task
# spec's wishlist item and the product's actual, intentional policy is
# recorded somewhere findable rather than silently absent.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# "User B cannot retrieve A's private ingestion material" -- not applicable
# as stated. Verified by search: `ingested_artifacts` (db/32_ingestion_
# provenance.sql) DOES carry owner_id/visibility columns, but no REST or
# MCP endpoint anywhere reads that table at all -- grep confirms its only
# readers are skill_ingestion.py's own idempotency lookups (by content_hash,
# during ingestion itself, not a retrieval API). There is no read surface
# for ANY user, owner included, to retrieve ingestion provenance material
# after the fact -- so there is no cross-user leak to test because there is
# no read path for anyone. This is a genuine "does not apply" rather than a
# skipped case: the boundary the spec asks about does not exist in this
# codebase's current surface area.
# ---------------------------------------------------------------------------
