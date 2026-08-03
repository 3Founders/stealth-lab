"""
`POST /v1/run` and the run views.

Two paths out of one entrypoint:

  match     an existing task already does this -> bind, typecheck, execute
  decompose nothing does -> propose a plan, typecheck it, park it for a human

Both build a `Plan` and hand it to the same executor. Keeping one execution
path rather than a fast one and a general one is what stops the two drifting
until only the fast one is really tested.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import build_services, get_pool
from app.models.run import RunRequest
from app.services import runs as run_store
from app.services.typecheck import typecheck, typecheck_report

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["run"])


@router.post("/run")
async def create_run(body: RunRequest, response: Response, pool=Depends(get_pool)) -> dict[str, Any]:
    services = build_services(pool)

    intake = await services.intake.assess(body.prompt, body.inputs)

    if intake.matched and intake.accepted is not None:
        plan = await services.graph.plan_for_task(intake.accepted.task)
        context = await services.graph.load_typecheck_context(plan)
        problems = typecheck(plan, context)
        if problems:
            # A matched task whose own plan does not typecheck is a broken
            # task definition, not a bad request. Surfaced rather than run.
            raise HTTPException(
                status_code=409,
                detail={
                    "error": f"task '{intake.accepted.task.name}' does not typecheck",
                    "typecheck": typecheck_report(problems),
                },
            )

        run_id = await run_store.create_run(
            pool, body.prompt, body.inputs, plan, status="running"
        )
        result = await services.executor.execute(
            plan, body.inputs, run_id=run_id,
            quality_bar=body.quality_bar, max_cost=body.max_cost,
        )
        await run_store.finish_run(pool, run_id, result)

        return {
            "route": "match",
            "run_id": str(run_id),
            "status": result.status,
            "matched_task": intake.accepted.task.name,
            "match_score": intake.accepted.score,
            "outputs": result.outputs,
            "error": result.error,
            "stages": [s.model_dump(mode="json") for s in result.stages],
            "total_cost": result.total_cost,
            "total_latency_ms": result.total_latency_ms,
        }

    decomposition = await services.decomposer.decompose(body.prompt, body.inputs)
    plan = decomposition.plan

    if decomposition.feasible:
        context = await services.graph.load_typecheck_context(plan)
        problems = typecheck(plan, context)
        report = typecheck_report(problems)
    else:
        report = {
            "ok": False,
            "problems": [
                {"rule": "decomposition", "message": m, "refs": []}
                for m in (decomposition.problems or ["no plan was produced"])
            ],
            "messages": decomposition.problems or ["no plan was produced"],
        }

    row = await pool.fetchrow(
        """
        INSERT INTO proposals (request_text, inputs, plan, typecheck, status)
        VALUES ($1,$2,$3,$4,'pending') RETURNING id
        """,
        body.prompt,
        body.inputs,
        plan.model_dump(mode="json"),
        report,
    )

    response.status_code = 202
    return {
        "route": "decompose",
        "proposal_id": str(row["id"]),
        "feasible": decomposition.feasible,
        "reasoning": decomposition.reasoning or intake.reason,
        "match_reason": intake.reason,
        "candidates": [
            {"id": str(c.task.id), "name": c.task.name, "score": c.score}
            for c in intake.candidates
        ],
        "typecheck": report,
        "approvable": report["ok"],
        "plan": plan.model_dump(mode="json"),
    }


@router.get("/runs")
async def list_runs(limit: int = 50, pool=Depends(get_pool)) -> list[dict[str, Any]]:
    return await run_store.list_runs(pool, limit=limit)


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, pool=Depends(get_pool)) -> dict[str, Any]:
    summary = await run_store.load_run(pool, run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="no such run")
    return summary.model_dump(mode="json")
