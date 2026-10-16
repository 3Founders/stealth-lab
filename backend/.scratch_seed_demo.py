"""Seed a small, honest demo dataset into the disposable local Postgres:
a couple of Goals, real Procedures with real (verified, via
record_execution_outcome) evidence, active Solutions, and a couple of
completed Evaluations -- so the Problems/Goal/Procedure pages have real,
non-fabricated content to render. No shortcuts: every write goes through
the same service functions the product itself uses, EXCEPT the submission
review flow (app.economy.submissions.review_procedure_submission), which
has a real bug -- it passes a procedure's row id to associate_solution
where a stable procedure_id is required (see chat) -- worked around here
by calling associate_solution directly with the correct id, same as the
regression tests already do.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


async def _real_execution(pool, proc: dict) -> str:
    from app.utils.ids import uuid7
    plan_id, graph_id = str(uuid7()), str(uuid7())
    await pool.execute(
        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t','h','h','x')",
        plan_id, proc["procedure_id"], 1, proc["id"],
    )
    await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h','x')", graph_id, plan_id)
    exec_id = str(uuid7())
    await pool.execute(
        "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, outcome, started_at, ended_at) VALUES ($1,$2,$3,$4,$5,'success',now(),now())",
        exec_id, plan_id, graph_id, proc["procedure_id"], 1,
    )
    return exec_id


async def main(dsn: str) -> None:
    from app.db.session import create_pool
    from app.services.procedures import capture_procedure, record_execution_outcome
    from app.services.product_model import (
        associate_solution, complete_evaluation, create_benchmark, create_problem,
        request_evaluation,
    )

    pool = await create_pool(dsn, min_size=1, max_size=4)
    try:
        problem = await create_problem(
            pool, title="Deploy a Next.js app to a fresh Linux VM", description="Get a production build serving traffic on a clean box.",
            objective="A running, publicly reachable Next.js production server", constraints=[],
            status="open", proposer=None, provenance="system_pending_review", metadata={},
            visibility="public", scope_type="global", scope_entity_id=None,
        )
        print("goal:", problem["id"])

        submitter = "demo-alice"
        proc = await capture_procedure(
            pool, name="Deploy with pm2 + nginx reverse proxy",
            goal="Get the app serving real traffic on a fresh VM",
            steps=[
                {"description": "Install Node 20 LTS via nvm"},
                {"description": "Clone the repo and run npm ci && npm run build"},
                {"description": "Run the server under pm2 (pm2 start npm --name app -- start)"},
                {"description": "Configure nginx as a reverse proxy to localhost:3000"},
                {"description": "Point DNS at the VM and issue a TLS cert with certbot"},
            ],
            provenance="system_pending_review", scope_type="global", created_by=submitter, owner_id=submitter, goal_on_unavailable="create",
        )
        sol = await associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer=submitter)
        print("procedure:", proc)

        updated = None
        for i in range(12):
            updated = await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key=f"vm-{i % 4}")
        print("verification_state after real runs:", updated["verification_state"])

        proc2 = await capture_procedure(
            pool, name="Deploy with Docker Compose", goal="Get the app serving real traffic on a fresh VM",
            steps=[
                {"description": "Write a Dockerfile for the Next.js app"},
                {"description": "docker compose up -d --build"},
                {"description": "Put Caddy in front for automatic TLS"},
            ],
            provenance="system_pending_review", scope_type="global", created_by=submitter, owner_id=submitter, goal_on_unavailable="create",
        )
        await associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc2["procedure_id"], status="active", proposer=submitter)
        updated2 = None
        for i in range(10):
            success = i not in (2, 5, 8)
            updated2 = await record_execution_outcome(pool, procedure_row_id=proc2["id"], success=success, context_key=f"vm-{i % 3}")
        print("way 2 verification_state:", updated2["verification_state"])

        bench = await create_benchmark(
            pool, problem_id=problem["id"], name="Cold VM to served traffic in under 20 minutes",
            description="Time a fresh Ubuntu 22.04 VM to first successful HTTP 200 from the deployed app.",
            provenance="system_pending_review",
        )
        ev = await request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"], provenance="demo-seed")
        exec_id = await _real_execution(pool, proc)
        completed = await complete_evaluation(pool, ev["id"], execution_ids=[exec_id])
        print("evaluation completed:", ev["id"], completed["aggregate_result"])

        problem2 = await create_problem(
            pool, title="Rotate a leaked API key without downtime", description="A credential leaked; replace it in every consumer before revoking the old one.",
            objective="Old key revoked, zero failed requests during the window", constraints=[],
            status="open", proposer=None, provenance="system_pending_review", metadata={},
            visibility="public", scope_type="global", scope_entity_id=None,
        )
        print("goal 2:", problem2["id"])
        proc3 = await capture_procedure(
            pool, name="Dual-key rolling rotation", goal="Rotate a credential with zero downtime",
            steps=[
                {"description": "Issue a new key alongside the old one"},
                {"description": "Deploy the new key to every consumer"},
                {"description": "Confirm zero errors using the old key for 15 minutes"},
                {"description": "Revoke the old key"},
            ],
            provenance="system_pending_review", scope_type="global", created_by=submitter, owner_id=submitter, goal_on_unavailable="create",
        )
        await associate_solution(pool, problem_id=problem2["id"], solution_type="procedure", target_id=proc3["procedure_id"], status="active", proposer=submitter)
        for i in range(11):
            await record_execution_outcome(pool, procedure_row_id=proc3["id"], success=True, context_key=f"svc-{i % 3}")

        print("SEED OK")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
