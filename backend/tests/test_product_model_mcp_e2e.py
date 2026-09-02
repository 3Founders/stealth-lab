"""
Live-database proof that the product-model tools are reachable THROUGH the
MCP server (directive §37): find_problem / inspect_problem /
list_problem_solutions / compare_solutions / inspect_evaluation /
find_best_solution -- each a thin read over app.services.product_model, the
same service the REST surface uses.

Skips without a real DATABASE_URL. Reuses the execution-chain fixtures from
test_product_model_e2e so this file only exercises the MCP layer.
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
from tests.test_product_model_e2e import _make_procedure, _run_executions  # noqa: E402


class _Ctx:
    def __init__(self, pool):
        class _RC:
            pass
        self.request_context = _RC()
        self.request_context.lifespan_context = {"pool": pool}


@pytest.mark.asyncio
async def test_product_model_tools_reachable_through_mcp():
    import app.mcp_server.server as srv

    pool = await create_pool(statement_cache_size=0)
    ctx = _Ctx(pool)
    tag = uuid.uuid4().hex[:8]
    try:
        problem = await pm.create_problem(
            pool, title=f"[pm-mcp {tag}] make the flaky test suite deterministic",
            objective="zero flaky failures across 100 runs", proposer="pm_mcp",
        )
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name="flake-bench", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"},
        )
        pa_id, pa_row = await _make_procedure(pool, f"pm-mcp-{tag}-A")
        pb_id, pb_row = await _make_procedure(pool, f"pm-mcp-{tag}-B")
        sa = await pm.associate_solution(pool, problem_id=problem["id"],
                                         solution_type="procedure", target_id=pa_id, status="active")
        sb = await pm.associate_solution(pool, problem_id=problem["id"],
                                         solution_type="procedure", target_id=pb_id, status="active")
        exec_a = await _run_executions(pool, pa_id, pa_row, n=20, successes=19)
        exec_b = await _run_executions(pool, pb_id, pb_row, n=20, successes=9)
        ea = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                         solution_id=sa["id"], procedure_id=pa_id, procedure_version=1,
                                         environment={"runtime": "linux"},
                                         methodology={"verification": "deterministic"})
        eb = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                         solution_id=sb["id"], procedure_id=pb_id, procedure_version=1,
                                         environment={"runtime": "linux"},
                                         methodology={"verification": "deterministic"})
        done_a = await pm.complete_evaluation(pool, ea["id"], execution_ids=exec_a)
        await pm.complete_evaluation(pool, eb["id"], execution_ids=exec_b)

        # --- find_problem ---
        fp = json.loads(await srv.find_problem("flaky deterministic test suite", ctx))
        assert any(p["id"] == problem["id"] for p in fp["problems"]), fp

        # --- inspect_problem ---
        ip = json.loads(await srv.inspect_problem(problem["id"], ctx))
        assert ip["problem"]["id"] == problem["id"]
        assert len(ip["benchmarks"]) == 1 and len(ip["solutions"]) == 2
        assert ip["leaderboard"]["current_best"] == [sa["id"]], ip["leaderboard"]

        assert (await srv.inspect_problem("00000000-0000-0000-0000-000000000000", ctx)
                ).startswith("REFUSED")

        # --- list_problem_solutions ---
        ls = json.loads(await srv.list_problem_solutions(problem["id"], ctx))
        assert {s["id"] for s in ls["solutions"]} == {sa["id"], sb["id"]}

        # --- compare_solutions ---
        cs = json.loads(await srv.compare_solutions(problem["id"], json.dumps([sa["id"], sb["id"]]), ctx))
        assert {e["solution_id"] for e in cs["leaderboard"]} == {sa["id"], sb["id"]}
        assert cs["current_best"] == [sa["id"]]
        cs1 = json.loads(await srv.compare_solutions(problem["id"], json.dumps([sb["id"]]), ctx))
        assert {e["solution_id"] for e in cs1["leaderboard"]} == {sb["id"]}
        assert cs1["current_best"] == []  # B alone is not BEST_VERIFIED
        assert (await srv.compare_solutions(problem["id"], "not json", ctx)).startswith("REFUSED")

        # --- inspect_evaluation ---
        ie = json.loads(await srv.inspect_evaluation(ea["id"], ctx))
        assert ie["id"] == ea["id"] and ie["status"] == "completed"
        assert len(ie["executions"]) == 20
        assert ie["verification_summary"]["verified_successes"] == 19
        assert (await srv.inspect_evaluation("00000000-0000-0000-0000-000000000000", ctx)
                ).startswith("REFUSED")

        # --- find_best_solution ---
        fbs = json.loads(await srv.find_best_solution("deterministic flaky test suite", ctx))
        assert fbs["result"] == "verified"
        assert fbs["current_best"] == [sa["id"]], fbs
    finally:
        await pool.close()
