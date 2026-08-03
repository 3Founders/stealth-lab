"""Scoring every implementation of a task against an eval's case set."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_pool
from app.runners import default_runners
from app.services.evals import EvalService

router = APIRouter(prefix="/v1/evals", tags=["evals"])


@router.post("/{eval_id}/run")
async def run_eval(eval_id: UUID, pool=Depends(get_pool)) -> dict[str, Any]:
    service = EvalService(pool, default_runners())
    try:
        result = await service.run(eval_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "eval_id": str(result.eval_id),
        "task_id": str(result.task_node_id),
        "cases": result.case_count,
        "scores": [
            {
                "implementation_id": str(s.implementation_id),
                "implementation": s.implementation_name,
                "score": s.score,
                "cost": s.cost,
                "latency_ms": s.latency_ms,
                "failures": s.failures,
            }
            for s in sorted(result.scores, key=lambda s: s.score, reverse=True)
        ],
    }


@router.get("/{eval_id}/results")
async def eval_results(eval_id: UUID, pool=Depends(get_pool)) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT r.id, r.implementation_id, i.name AS implementation, r.score, r.cost,
               r.latency_ms, r.detail, r.ran_at
        FROM eval_results r
        JOIN implementations i ON i.id = r.implementation_id
        WHERE r.eval_id = $1
        ORDER BY r.ran_at DESC
        LIMIT 200
        """,
        eval_id,
    )
    return [
        {
            "id": str(r["id"]),
            "implementation_id": str(r["implementation_id"]),
            "implementation": r["implementation"],
            "score": float(r["score"]),
            "cost": float(r["cost"]),
            "latency_ms": r["latency_ms"],
            "detail": r["detail"],
            "ran_at": r["ran_at"],
        }
        for r in rows
    ]
