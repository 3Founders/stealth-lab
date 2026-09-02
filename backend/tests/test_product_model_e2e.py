"""
§57 product-model lineage E2E, against real PostgreSQL.

Proves the whole chain WITHOUT a hand-written "final best" row:

    Problem -> Benchmark v1 -> Solution A + Solution B
            -> real execution_plans / task_graphs / executions / evidence
            -> request_evaluation -> complete_evaluation (metrics RECOMPUTED
               from the linked executions, not from the caller)
            -> problem_leaderboard -> current_best derived from Wilson lower
            -> same answer via REST GET /v1/problems/{id}/leaderboard

Also proves §16: an evaluation cannot be completed with no execution lineage
(service raises; the DB trigger is the backstop).

Skips itself when DATABASE_URL is unset (offline-suite convention).
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        h = abs(hash(text))
        return [((h >> (i % 40)) & 1) * 0.1 + 0.01 for i in range(1024)]


async def _make_procedure(pool, name):
    res = await capture_procedure(
        pool, name=name, goal=f"{name} goal", steps=[{"order": 0, "goal": "do it"}],
        provenance="prior_library", scope_type="global", created_by="pm_e2e",
        embedding=await _FakeEmbedder().embed_one(name),
    )
    return res["procedure_id"], res["id"]  # (logical id, version-row id)


async def _run_executions(pool, procedure_id, procedure_row_id, *, n, successes):
    """Insert a real plan + graph + n executions; attach supporting
    evidence to the successful ones. Returns the execution id list."""
    async with pool.acquire() as conn:
        pv = await conn.fetchval(
            "SELECT version FROM procedures WHERE id=$1", procedure_row_id,
        )
        plan_id = await conn.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), procedure_id, pv, procedure_row_id,
            "pm-e2e benchmark run", f"pch-{uuid.uuid4().hex[:12]}", f"ch-{uuid.uuid4().hex[:12]}",
        )
        graph_id = await conn.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"h-{uuid.uuid4().hex[:12]}", json.dumps({"nodes": []}),
        )
        ids = []
        for i in range(n):
            outcome = "success" if i < successes else "failure"
            eid = await conn.fetchval(
                "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, "
                " procedure_version, started_at, ended_at, outcome, created_by, scope_type) "
                "VALUES ($1,$2,$3,$4,$5, now() - interval '10 seconds', now(), $6, 'pm_e2e','global') "
                "RETURNING id",
                str(uuid7()), plan_id, graph_id, procedure_id, pv, outcome,
            )
            ids.append(str(eid))
        # evidence targets the PROCEDURE VERSION (evidence_target_type_chk
        # allows claim|procedure|implementation, not 'execution'); one
        # supporting execution_result per success.
        for _ in range(successes):
            await conn.execute(
                "INSERT INTO evidence (id, evidence_type, target_type, target_id, "
                " target_version, direction, strength_score, strength_method, "
                " outcome_status, success_criteria, created_by) "
                "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,'e2e_probe',"
                " 'success', $4::jsonb, 'pm_e2e')",
                str(uuid7()), procedure_id, pv,
                json.dumps({"predicate": "benchmark_case_passed"}),
            )
        return ids


@pytest.mark.asyncio
async def test_product_model_lineage_and_current_best_are_derived_not_stored():
    pool = await create_pool(statement_cache_size=0)
    scope = AccessScope.unrestricted()
    tag = uuid.uuid4().hex[:8]
    try:
        problem = await pm.create_problem(
            pool, title=f"[pm-e2e {tag}] reduce coding-agent context",
            objective="fewer input tokens at equal task success", proposer="pm_e2e",
        )
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name="ctx-bench", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"},
        )

        proc_a_id, proc_a_row = await _make_procedure(pool, f"pm-e2e-{tag}-solution-A")
        proc_b_id, proc_b_row = await _make_procedure(pool, f"pm-e2e-{tag}-solution-B")
        sol_a = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=proc_a_id, proposer="pm_e2e", status="active",
        )
        sol_b = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=proc_b_id, proposer="pm_e2e", status="active",
        )

        # A: strong (28/30 verified). B: weak (12/30).
        exec_a = await _run_executions(pool, proc_a_id, proc_a_row, n=30, successes=28)
        exec_b = await _run_executions(pool, proc_b_id, proc_b_row, n=30, successes=12)

        eval_a = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol_a["id"],
            procedure_id=proc_a_id, procedure_version=1,
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"},
        )
        eval_b = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol_b["id"],
            procedure_id=proc_b_id, procedure_version=1,
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"},
        )

        # §16: cannot complete with no lineage.
        with pytest.raises(ValueError):
            await pm.complete_evaluation(pool, eval_a["id"], execution_ids=[])

        done_a = await pm.complete_evaluation(pool, eval_a["id"], execution_ids=exec_a)
        done_b = await pm.complete_evaluation(pool, eval_b["id"], execution_ids=exec_b)

        # metrics were RECOMPUTED from the linked executions, not supplied.
        assert done_a["metrics"]["run_count"] == 30
        assert done_a["verification_summary"]["verified_successes"] == 28
        assert done_a["verification_summary"]["source"] == "recomputed_from_evaluation_executions"
        assert done_a["metrics"]["verified_success_wilson_lower"] > done_b["metrics"]["verified_success_wilson_lower"]

        lb = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        assert lb["current_best"] == [sol_a["id"]], lb
        assert lb["current_best_is_tie"] is False
        by_sol = {e["solution_id"]: e for e in lb["leaderboard"]}
        assert by_sol[sol_a["id"]]["state"] == "BEST_VERIFIED"
        assert by_sol[sol_b["id"]]["state"] in ("HIGH_PERFORMING", "PROMISING", "INSUFFICIENT_EVIDENCE")
        assert by_sol[sol_a["id"]]["run_count"] == 30 and by_sol[sol_a["id"]]["verified_successes"] == 28

        # no stored winner: the problem/solution rows carry no 'winner'/'best' field
        prob_row = await pm.get_problem(pool, problem["id"], scope=scope)
        assert "winner" not in prob_row and "best_solution_id" not in prob_row

        # §57 REST leg: the same current_best via the HTTP surface.
        import httpx
        from app.api import problems as problems_api

        transport = httpx.ASGITransport(app=_solo_app(problems_api))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(f"/v1/problems/{problem['id']}/leaderboard")
            assert r.status_code == 200, r.text
            assert r.json()["current_best"] == [sol_a["id"]]
            r2 = await c.get("/v1/best-way", params={"goal": "reduce coding agent context tokens"})
            assert r2.status_code == 200
            body = r2.json()
            assert body["result"] in ("verified", "no verified solution yet")
    finally:
        await pool.close()


def _solo_app(problems_api):
    from fastapi import FastAPI
    from app.db.session import get_pool

    app = FastAPI()

    @app.middleware("http")
    async def _attach_pool(request, call_next):
        request.app.state.pool = await get_pool()
        return await call_next(request)

    app.include_router(problems_api.router)
    return app
