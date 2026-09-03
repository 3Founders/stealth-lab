"""
Problem / Benchmark / Solution / Evaluation REST surface (directive §36, §38).

Thin: every route hydrates scope and delegates to
`app.services.product_model`. No ranking or lineage logic here -- REST and
MCP both converge on that one service (§37).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import get_scope
from app.services import product_model as pm
from app.services.access import AccessScope

router = APIRouter(prefix="/v1", tags=["product-model"])


async def get_pool(request: Request):
    return request.app.state.pool


# ---- request bodies -------------------------------------------------------
class ProblemIn(BaseModel):
    title: str
    description: Optional[str] = None
    objective: Optional[str] = None
    constraints: list[Any] = Field(default_factory=list)
    status: str = "open"
    proposer: Optional[str] = None
    provenance: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    visibility: str = "public"
    scope_type: Optional[str] = None
    scope_entity_id: Optional[str] = None


class BenchmarkIn(BaseModel):
    problem_id: str
    name: str
    description: Optional[str] = None
    version: int = 1
    evaluation_protocol: dict[str, Any] = Field(default_factory=dict)
    environment_specification: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    comparison_policy: dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SolutionIn(BaseModel):
    problem_id: str
    solution_type: str
    target_id: str
    version: int = 1
    status: str = "proposed"
    proposer: Optional[str] = None
    provenance: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationIn(BaseModel):
    problem_id: str
    benchmark_id: str
    solution_id: str
    procedure_id: Optional[str] = None
    procedure_version: Optional[int] = None
    implementation_id: Optional[str] = None
    implementation_version: Optional[int] = None
    environment: dict[str, Any] = Field(default_factory=dict)
    methodology: dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[str] = None


class EvaluationCompleteIn(BaseModel):
    execution_ids: list[str]
    aggregate_result: Optional[str] = None
    extra_metrics: dict[str, Any] = Field(default_factory=dict)


# ---- Problem ------------------------------------------------------------
@router.post("/problems")
async def create_problem(body: ProblemIn, pool=Depends(get_pool),
                         scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.create_problem(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/problems")
async def list_problems(status: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                        pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"problems": await pm.list_problems(pool, scope=scope, status=status, limit=limit)}


@router.get("/problems/find")
async def find_problems(q: str, limit: int = Query(10, ge=1, le=50), pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"query": q, "problems": await pm.find_problem(pool, q, scope=scope, limit=limit)}


@router.get("/best-way")
async def best_way(goal: str, pool=Depends(get_pool),
                   scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return await pm.find_best_way(pool, goal, scope=scope)


@router.get("/problems/{problem_id}")
async def get_problem(problem_id: str, pool=Depends(get_pool),
                      scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    p = await pm.get_problem(pool, problem_id, scope=scope)
    if p is None:
        raise HTTPException(status_code=404, detail="problem not found or out of scope")
    return p


@router.get("/problems/{problem_id}/solutions")
async def problem_solutions(problem_id: str, pool=Depends(get_pool),
                            scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"solutions": await pm.list_problem_solutions(pool, problem_id, scope=scope)}


@router.get("/problems/{problem_id}/benchmarks")
async def problem_benchmarks(problem_id: str, pool=Depends(get_pool),
                             scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"benchmarks": await pm.list_problem_benchmarks(pool, problem_id, scope=scope)}


@router.get("/problems/{problem_id}/evaluations")
async def problem_evaluations(problem_id: str, pool=Depends(get_pool),
                              scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"evaluations": await pm.list_problem_evaluations(pool, problem_id, scope=scope)}


@router.get("/problems/{problem_id}/leaderboard")
async def problem_leaderboard(problem_id: str, benchmark_id: Optional[str] = None,
                              pool=Depends(get_pool),
                              scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    if await pm.get_problem(pool, problem_id, scope=scope) is None:
        raise HTTPException(status_code=404, detail="problem not found or out of scope")
    return await pm.problem_leaderboard(pool, problem_id, scope=scope, benchmark_id=benchmark_id)


# ---- Benchmark -------------------------------------------------------------
@router.post("/benchmarks")
async def create_benchmark(body: BenchmarkIn, pool=Depends(get_pool),
                           scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.create_benchmark(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/benchmarks/{benchmark_id}/freeze")
async def freeze_benchmark(benchmark_id: str, pool=Depends(get_pool),
                           scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.freeze_benchmark(pool, benchmark_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/benchmarks/{benchmark_id}")
async def get_benchmark(benchmark_id: str, pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    b = await pm.get_benchmark(pool, benchmark_id, scope=scope)
    if b is None:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return b


# ---- Solution -----------------------------------------------------------
@router.post("/solutions/associate")
async def associate_solution(body: SolutionIn, pool=Depends(get_pool),
                             scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.associate_solution(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---- Evaluation --------------------------------------------------------
@router.post("/evaluations")
async def request_evaluation(body: EvaluationIn, pool=Depends(get_pool),
                             scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.request_evaluation(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/evaluations/{evaluation_id}/complete")
async def complete_evaluation(evaluation_id: str, body: EvaluationCompleteIn,
                              pool=Depends(get_pool),
                              scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.complete_evaluation(
            pool, evaluation_id, execution_ids=body.execution_ids,
            aggregate_result=body.aggregate_result, extra_metrics=body.extra_metrics,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/evaluations/{evaluation_id}/invalidate")
async def invalidate_evaluation(evaluation_id: str, reason: str = Query(...),
                                pool=Depends(get_pool),
                                scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.invalidate_evaluation(pool, evaluation_id, reason=reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, pool=Depends(get_pool),
                         scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    e = await pm.get_evaluation(pool, evaluation_id, scope=scope)
    if e is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return e
