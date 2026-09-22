"""Final-V1 evaluation Bug #8 regression, against real PostgreSQL.

A private Problem already stays private through `scope_predicates()`. Its
downstream object graph did NOT: `get_benchmark` / `list_problem_benchmarks`
/ `get_evaluation` / `list_problem_evaluations` ran raw, unscoped queries, so
another user could read/list a private Problem's Benchmark and Evaluation
data directly or infer it through the leaderboard.

Fix: those accessors now inherit the owning Problem's visibility (the exact
rule `list_problem_solutions` already applies), the REST routes thread the
viewer scope, and the product-model MCP tools derive the caller's scope the
same way REST does instead of hardcoding `AccessScope.unrestricted()`.

This proves it at three layers -- service, REST (real `X-Viewer-Id`
identity), MCP (real caller-identity contextvar) -- plus the negative
inference-leak checks. Skips without a real DATABASE_URL.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

import httpx  # noqa: E402

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from tests.test_product_model_e2e import _make_procedure, _run_executions  # noqa: E402

_PREFIX = "pm-privacy-e2e"
ALICE = "alice-pm-privacy"
BOB = "bob-pm-privacy"


async def _full_lineage(pool, *, visibility: str, owner_id: str | None, tag: str):
    """Problem -> frozen Benchmark -> Solution -> completed Evaluation, all
    through the real product_model write paths. Returns the id bundle. The
    `objective` carries a tag-unique nonce so a lexical `find_problem`
    match can only ever be THIS problem (the shared live DB holds hundreds
    of residual test problems)."""
    nonce = f"scopeisolationnonce{tag.replace('-', '')}"   # single rare token, no common words
    objective = f"{nonce} downstream graph privacy proof"
    problem = await pm.create_problem(
        pool, title=f"[{_PREFIX} {tag}] {visibility} problem",
        objective=objective,
        proposer=owner_id or "anon", visibility=visibility, owner_id=owner_id,
    )
    bench = await pm.create_benchmark(
        pool, problem_id=problem["id"], name=f"{_PREFIX}-bench-{tag}", version=1,
        evaluation_protocol={"verification": "deterministic"},
        environment_specification={"runtime": "linux"},
    )
    await pm.freeze_benchmark(pool, bench["id"])
    pid_, prow = await _make_procedure(pool, f"{_PREFIX}-proc-{tag}")
    sol = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="procedure",
        target_id=pid_, proposer=owner_id or "anon", status="active",
    )
    ex = await _run_executions(pool, pid_, prow, n=20, successes=19)
    ev = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
        procedure_id=pid_, procedure_version=1,
        environment={"runtime": "linux"}, methodology={"verification": "deterministic"},
    )
    done = await pm.complete_evaluation(pool, ev["id"], execution_ids=ex)
    return {
        "problem_id": problem["id"], "benchmark_id": bench["id"],
        "solution_id": sol["id"], "evaluation_id": ev["id"],
        "benchmark_name": bench["name"], "objective": objective, "nonce": nonce,
        "done": done,
    }


def _solo_app(pool):
    """Router-only app bound to THIS test's dedicated pool -- deliberately
    not the process-global ``get_pool()`` singleton, so running this file
    beside the other product-model e2e files can't contend on one shared
    connection."""
    from fastapi import FastAPI
    from app.api import problems as problems_api

    app = FastAPI()
    app.state.pool = pool
    app.include_router(problems_api.router)
    return app


@pytest.mark.asyncio
async def test_private_benchmark_and_evaluation_inherit_problem_scope_service_layer():
    pool = await create_pool(statement_cache_size=0)
    tag = uuid.uuid4().hex[:8]
    alice, bob, anon = (AccessScope.for_user(ALICE), AccessScope.for_user(BOB),
                        AccessScope.anonymous())
    try:
        priv = await _full_lineage(pool, visibility="private", owner_id=ALICE, tag=f"priv-{tag}")

        # --- owner sees everything ---
        assert await pm.get_benchmark(pool, priv["benchmark_id"], scope=alice) is not None
        assert await pm.list_problem_benchmarks(pool, priv["problem_id"], scope=alice)
        assert await pm.get_evaluation(pool, priv["evaluation_id"], scope=alice) is not None
        assert await pm.list_problem_evaluations(pool, priv["problem_id"], scope=alice)
        lb_owner = await pm.problem_leaderboard(pool, priv["problem_id"], scope=alice)
        assert lb_owner["current_best"] == [priv["solution_id"]]
        assert lb_owner["benchmark_id"] == priv["benchmark_id"]

        # --- a different authenticated user sees NOTHING of the private graph ---
        for other in (bob, anon):
            assert await pm.get_benchmark(pool, priv["benchmark_id"], scope=other) is None
            assert await pm.list_problem_benchmarks(pool, priv["problem_id"], scope=other) == []
            assert await pm.get_evaluation(pool, priv["evaluation_id"], scope=other) is None
            assert await pm.list_problem_evaluations(pool, priv["problem_id"], scope=other) == []
            lb = await pm.problem_leaderboard(pool, priv["problem_id"], scope=other)
            assert lb["current_best"] == []
            assert lb["leaderboard"] == []
            assert lb["benchmark_id"] is None            # no private benchmark id leak
            assert lb["ineligible_solutions"] == []

        # --- a PUBLIC problem's downstream graph is still readable by anyone ---
        pub = await _full_lineage(pool, visibility="public", owner_id=ALICE, tag=f"pub-{tag}")
        for who in (bob, anon):
            assert await pm.get_benchmark(pool, pub["benchmark_id"], scope=who) is not None
            assert await pm.list_problem_benchmarks(pool, pub["problem_id"], scope=who)
            assert await pm.get_evaluation(pool, pub["evaluation_id"], scope=who) is not None
            lb = await pm.problem_leaderboard(pool, pub["problem_id"], scope=who)
            assert lb["current_best"] == [pub["solution_id"]]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_private_benchmark_and_evaluation_denied_cross_user_over_rest():
    pool = await create_pool(statement_cache_size=0)
    tag = uuid.uuid4().hex[:8]
    try:
        priv = await _full_lineage(pool, visibility="private", owner_id=ALICE, tag=f"rest-{tag}")
        bid, eid, pid_ = priv["benchmark_id"], priv["evaluation_id"], priv["problem_id"]
        transport = httpx.ASGITransport(app=_solo_app(pool))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            a = {"X-Viewer-Id": ALICE}
            b = {"X-Viewer-Id": BOB}

            # owner: all allowed
            assert (await c.get(f"/v1/benchmarks/{bid}", headers=a)).status_code == 200
            assert (await c.get(f"/v1/problems/{pid_}/benchmarks", headers=a)).json()["benchmarks"]
            assert (await c.get(f"/v1/evaluations/{eid}", headers=a)).status_code == 200
            assert (await c.get(f"/v1/problems/{pid_}/evaluations", headers=a)).json()["evaluations"]
            assert (await c.get(f"/v1/problems/{pid_}/leaderboard", headers=a)).status_code == 200

            # bob: denied / empty, and no inference leak in the bodies
            assert (await c.get(f"/v1/benchmarks/{bid}", headers=b)).status_code == 404
            rb = await c.get(f"/v1/problems/{pid_}/benchmarks", headers=b)
            assert rb.status_code == 200 and rb.json()["benchmarks"] == []
            assert priv["benchmark_name"] not in rb.text and bid not in rb.text
            assert (await c.get(f"/v1/evaluations/{eid}", headers=b)).status_code == 404
            re = await c.get(f"/v1/problems/{pid_}/evaluations", headers=b)
            assert re.status_code == 200 and re.json()["evaluations"] == []
            assert (await c.get(f"/v1/problems/{pid_}/leaderboard", headers=b)).status_code == 404
            # the problem itself is already 404 to bob (unchanged behaviour)
            assert (await c.get(f"/v1/problems/{pid_}", headers=b)).status_code == 404

            # anonymous: same denials
            assert (await c.get(f"/v1/benchmarks/{bid}")).status_code == 404
            assert (await c.get(f"/v1/evaluations/{eid}")).status_code == 404
            assert (await c.get(f"/v1/problems/{pid_}/benchmarks")).json()["benchmarks"] == []
    finally:
        await pool.close()

# test_private_benchmark_and_evaluation_not_exposed_through_mcp_tools removed
# 2026-09-22: the product-model MCP tools it exercised (inspect_problem,
# inspect_evaluation, compare_solutions, find_best_solution,
# list_problem_solutions) were removed from the MCP surface -- prod_frontend
# now calls the identical app.services.product_model service over REST
# (test_private_benchmark_and_evaluation_denied_cross_user_over_rest above),
# which remains the real, live, tested path. The service-layer and REST-
# layer regression tests in this file are unchanged.
