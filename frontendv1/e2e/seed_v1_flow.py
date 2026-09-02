"""
Seed one DETERMINISTIC Final-V1 product-path fixture into the live
Supabase/Postgres substrate, for the scripted browser E2E
(frontendv1/e2e/v1-flow.spec.ts).

Builds the real lineage -- no hand-written "best" row:

    Problem -> frozen Benchmark v1 -> Solution A + Solution B
            -> real execution_plans / task_graphs / executions / evidence
            -> request_evaluation -> complete_evaluation (metrics RECOMPUTED
               from the linked executions)
            -> problem_leaderboard.current_best == [Solution A]  (Wilson LB)

Reuses the exact write paths the product exercises. Prints a JSON blob and
writes it to frontendv1/e2e/.seed.json for the spec to read.

    cd backend && DATABASE_URL=<supabase> python ../frontendv1/e2e/seed_v1_flow.py

Idempotent-ish: each run creates a fresh, uniquely-tagged Problem so repeat
runs never collide. Old fixtures stay (the tables are append-only) and are
harmless.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import uuid

# import from the backend package
_HERE = pathlib.Path(__file__).resolve()
_BACKEND = _HERE.parents[2] / "backend"
sys.path.insert(0, str(_BACKEND))

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
        provenance="prior_library", scope_type="global", created_by="v1_browser_e2e",
        embedding=await _FakeEmbedder().embed_one(name),
    )
    return res["procedure_id"], res["id"]


async def _run_executions(pool, procedure_id, procedure_row_id, *, n, successes):
    async with pool.acquire() as conn:
        pv = await conn.fetchval(
            "SELECT version FROM procedures WHERE id=$1", procedure_row_id,
        )
        plan_id = await conn.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), procedure_id, pv, procedure_row_id,
            "v1-browser-e2e benchmark run",
            f"pch-{uuid.uuid4().hex[:12]}", f"ch-{uuid.uuid4().hex[:12]}",
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
                "VALUES ($1,$2,$3,$4,$5, now() - interval '10 seconds', now(), $6, "
                " 'v1_browser_e2e','global') RETURNING id",
                str(uuid7()), plan_id, graph_id, procedure_id, pv, outcome,
            )
            ids.append(str(eid))
        for _ in range(successes):
            await conn.execute(
                "INSERT INTO evidence (id, evidence_type, target_type, target_id, "
                " target_version, direction, strength_score, strength_method, "
                " outcome_status, success_criteria, created_by) "
                "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,'e2e_probe',"
                " 'success', $4::jsonb, 'v1_browser_e2e')",
                str(uuid7()), procedure_id, pv,
                json.dumps({"predicate": "benchmark_case_passed"}),
            )
        return ids


async def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL unset -- refusing to seed against nothing", file=sys.stderr)
        sys.exit(2)
    pool = await create_pool(statement_cache_size=0)
    scope = AccessScope.unrestricted()
    tag = uuid.uuid4().hex[:8]
    try:
        problem = await pm.create_problem(
            pool,
            title=f"[v1-browser-e2e {tag}] cut coding-agent context tokens",
            objective="fewer input tokens at equal task success",
            proposer="v1_browser_e2e",
        )
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name=f"ctx-bench-{tag}", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"},
        )
        await pm.freeze_benchmark(pool, bench["id"])

        proc_a_id, proc_a_row = await _make_procedure(pool, f"v1-e2e-{tag}-solution-A")
        proc_b_id, proc_b_row = await _make_procedure(pool, f"v1-e2e-{tag}-solution-B")
        sol_a = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=proc_a_id, proposer="v1_browser_e2e", status="active",
        )
        sol_b = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=proc_b_id, proposer="v1_browser_e2e", status="active",
        )

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
        done_a = await pm.complete_evaluation(pool, eval_a["id"], execution_ids=exec_a)
        await pm.complete_evaluation(pool, eval_b["id"], execution_ids=exec_b)

        lb = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        assert lb["current_best"] == [sol_a["id"]], lb
        assert done_a["metrics"]["run_count"] == 30
        assert done_a["verification_summary"]["verified_successes"] == 28

        seed = {
            "problem_id": problem["id"],
            "problem_title": problem["title"],
            "benchmark_id": bench["id"],
            "benchmark_name": bench["name"],
            "solution_a_id": sol_a["id"],
            "solution_b_id": sol_b["id"],
            "evaluation_a_id": eval_a["id"],
            "evaluation_b_id": eval_b["id"],
            "current_best": lb["current_best"],
            "viewer_id": "v1-browser-e2e-viewer",
        }
        out = _HERE.parent / ".seed.json"
        out.write_text(json.dumps(seed, indent=2))
        print(json.dumps(seed, indent=2))
        print(f"\nwrote {out}", file=sys.stderr)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
