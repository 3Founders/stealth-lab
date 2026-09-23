"""
Problem / Benchmark / Solution / Evaluation REST surface (directive §36, §38).

Thin: every route hydrates scope and delegates to
`app.services.product_model`. No ranking or lineage logic here -- REST and
MCP both converge on that one service (§37).
"""
from __future__ import annotations

from typing import Any, Optional

from app.services import auth_context as _ac
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user, require_scopes
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
    environment: dict[str, Any] = Field(default_factory=dict)
    methodology: dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[str] = None


class EvaluationCompleteIn(BaseModel):
    execution_ids: list[str]
    aggregate_result: Optional[str] = None
    extra_metrics: dict[str, Any] = Field(default_factory=dict)


# ---- Problem ------------------------------------------------------------
@router.post("/problems")
async def create_problem(
    body: ProblemIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """`proposer`/`owner_id` are derived from the verified session, never
    accepted in the request body -- same rule every other write route in
    this codebase follows (see app/api/deps.py::get_scope's own doc, and
    the frontend's contribute pages' own stated guarantee). A caller could
    previously submit a Problem crediting anyone they liked; ProblemIn no
    longer even has a `proposer` field to close that off structurally, not
    just by convention."""
    try:
        return await pm.create_problem(
            pool, proposer=principal.name or principal.subject, owner_id=principal.subject,
            **body.model_dump(),
        )
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
    # B1 hardening, defense in depth: the public Goal page reads this
    # endpoint directly. Even though the association API can no longer
    # itself create a 'proposed' row that later gets silently promoted,
    # this filter means an unreviewed association could NEVER surface
    # here even if some other internal writer ever created one at a
    # status other than 'active' -- 'active' is set in exactly one place,
    # app.economy.submissions.review_procedure_submission/
    # review_benchmark_submission, once a human has accepted the submission.
    all_solutions = await pm.list_problem_solutions(pool, problem_id, scope=scope)
    return {"solutions": [s for s in all_solutions if s.get("status") == "active"]}


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
@router.post("/benchmarks", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def create_benchmark(body: BenchmarkIn, pool=Depends(get_pool),
                           scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.create_benchmark(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# B3 hardening (audit finding: any authenticated user, holding only the
# baseline KNOWLEDGE_WRITE scope, could freeze ANY benchmark -- and the
# Goal page reads frozen==verified). Freezing is now: (1) a reviewer-only
# operation (KNOWLEDGE_PUBLISH, the same scope every other review action
# in this codebase already requires), (2) only possible once the
# benchmark has actually been through the canonical review workflow
# (app.economy.submissions.review_benchmark_submission has accepted a
# benchmark_submissions row pointing at it) -- a benchmark created
# directly via POST /v1/problems/benchmarks (still just a 'draft' row,
# unchanged) has no such row and can never be frozen through this route,
# and (3) audited: actor/target/reason recorded via the same
# record_audit_event ticket-09 private-object-creation already uses.
@router.post("/benchmarks/{benchmark_id}/freeze", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_PUBLISH))])
async def freeze_benchmark(
    benchmark_id: str, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    accepted_submission = await pool.fetchval(
        "SELECT id FROM benchmark_submissions WHERE benchmark_id = $1 AND status = 'accepted' LIMIT 1",
        benchmark_id,
    )
    if accepted_submission is None:
        raise HTTPException(
            status_code=409,
            detail="this benchmark has no accepted submission on record -- it must go through "
                   "the contribution review workflow (POST /v1/economy/benchmark-submissions, "
                   "then a reviewer's .../review) before it can be frozen",
        )
    try:
        result = await pm.freeze_benchmark(pool, benchmark_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        from app.services.audit import record_audit_event

        await record_audit_event(
            pool, actor_subject=principal.subject, action="benchmark_frozen",
            object_type="benchmark", object_id=benchmark_id, actor_user_id=principal.user_id,
            details={"submission_id": str(accepted_submission)},
        )
    except Exception:  # noqa: BLE001 -- audit logging is additive, never fatal to the real action
        import logging

        logging.getLogger(__name__).warning("audit write failed for benchmark_frozen")
    return result


@router.get("/benchmarks/{benchmark_id}")
async def get_benchmark(benchmark_id: str, pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    b = await pm.get_benchmark(pool, benchmark_id, scope=scope)
    if b is None:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return b


# ---- Solution -----------------------------------------------------------
# B1 hardening (audit finding: this endpoint was a second, unreviewed path
# to a "live" association -- a caller could set status='active' and an
# arbitrary `proposer` directly, with no relationship at all to
# app.economy.submissions's procedure_submissions/benchmark_submissions
# review workflow). This is now the ONE public entry point for a
# client-initiated association, and it can only ever create a 'proposed'
# one: identity is server-derived, and status is forced regardless of
# what the body asks for. It is NOT a second path to 'active' -- the only
# way a solution reaches 'active' (and therefore appears on a Goal page /
# is ranked -- see list_problem_solutions's status filter below) is
# app.economy.submissions.review_procedure_submission/
# review_benchmark_submission calling app.services.product_model.
# associate_solution DIRECTLY (a Python call, not this HTTP route) once a
# human has accepted the submission.
@router.post("/solutions/associate", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def associate_solution(
    body: SolutionIn, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    # Never let a public call downgrade an association the canonical
    # review workflow already made 'active' -- the ON CONFLICT upsert
    # below would otherwise let anyone "un-list" an accepted Procedure/
    # Benchmark just by re-posting the same association.
    existing = await pool.fetchrow(
        "SELECT * FROM solutions WHERE problem_id=$1 AND solution_type=$2 AND target_id=$3 AND version=$4",
        body.problem_id, body.solution_type, body.target_id, body.version,
    )
    if existing is not None and existing["status"] == "active":
        return dict(existing)

    payload = body.model_dump()
    payload["proposer"] = principal.subject
    payload["status"] = "proposed"
    try:
        return await pm.associate_solution(pool, **payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---- Evaluation --------------------------------------------------------
@router.post("/evaluations", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def request_evaluation(body: EvaluationIn, pool=Depends(get_pool),
                             scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.request_evaluation(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/evaluations/{evaluation_id}/complete", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
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


@router.post("/evaluations/{evaluation_id}/invalidate", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
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
