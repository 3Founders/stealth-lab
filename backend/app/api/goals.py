"""
Goal REST API (ingestion.md Sec 18-19/25), including the Benchmark/
Solution/Evaluation product layer that used to live in the now-retired
app/api/problems.py (migration 110 folded "Problem" into "Goal" -- they
were the same real-world thing represented twice; see that migration's
own header for why). Everything here mirrors app/api/procedures.py's own
conventions (get_scope for reads, require_authenticated_user +
privacy-aware Embedder for user-facing writes). The MCP tools
(search_goals/inspect_goal/create_goal/list_goal_procedures in
app/mcp_server/server.py) wrap the SAME app.services.goals functions the
Goal-only routes below call -- no duplicated logic, two thin callers of
one real implementation. The Benchmark/Solution/Evaluation routes below
delegate to app.services.product_model the same way.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user, require_scopes
from app.services import auth_context as _ac
from app.services import product_model as pm
from app.services.access import AccessScope
from app.services.goals import create_goal_from_user, get_goal, search_goals
from app.services.v0_gate import V0Violation

router = APIRouter(prefix="/v1/goals", tags=["goals"])


async def get_pool(request: Request):
    return request.app.state.pool


def _owner_key(principal: AuthenticatedPrincipal) -> str:
    return principal.subject


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------
@router.get("")
async def list_goals_route(
    status: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
    pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """Plain recency-ordered listing -- the product surface's "browse all
    goals" need, distinct from /search below (which requires a query)."""
    return {"goals": await pm.list_goals(pool, scope=scope, status=status, limit=limit)}


@router.get("/find")
async def find_goals_route(
    q: str, limit: int = Query(10, ge=1, le=50), pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    return {"query": q, "goals": await pm.find_goal(pool, q, scope=scope, limit=limit)}


@router.get("/search")
async def search_goals_route(
    q: str,
    scope_type: Optional[str] = Query(default=None),
    scope_entity_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    semantic: bool = Query(
        default=False,
        description="Also embed `q` and RRF-fuse a semantic leg -- one real "
        "embedding API call. Off by default so a search-as-you-type UI "
        "doesn't pay for an embedding on every keystroke.",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict[str, Any]]:
    """Registered before /{goal_id} for the same readability reason
    /v1/procedures/search is (the UUID path convertor there already
    rejects the literal segment `search` on its own; goal_id here is a
    plain str path param, so this ordering is what actually matters)."""
    query_embedding = None
    if semantic:
        from app.services.embeddings import Embedder

        query_embedding, _meta = await Embedder().embed_one_with_metadata(q, input_type="query")

    results = await search_goals(
        pool, query_text=q, query_embedding=query_embedding,
        scope=scope, status=status, limit=limit,
    )
    if scope_type is not None:
        results = [
            r for r in results
            if r.get("scope_type") == scope_type and r.get("scope_entity_id") == scope_entity_id
        ]
    return results


@router.get("/{goal_id}")
async def inspect_goal_route(
    goal_id: str,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """A missing id and one that exists-but-isn't-visible are the same
    404 -- no enumeration signal, same posture every other single-row-by-id
    route in this codebase uses (app/api/graph.py's own documented rule)."""
    result = await get_goal(pool, goal_id, scope=scope)
    if result is None:
        raise HTTPException(404, "goal not found")
    return result


class GoalCreateBody(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=4000)
    objective: Optional[str] = Field(default=None, max_length=4000)
    constraints: list[Any] = Field(default_factory=list)
    scope_type: str = Field(default="global")
    scope_entity_id: Optional[str] = Field(default=None)
    allow_create_anyway: bool = Field(
        default=False,
        description="Set true after reviewing near_matches (a prior call's "
        "response) to confirm this is genuinely a new, distinct goal.",
    )
    use_embeddings: bool = Field(
        default=True,
        description="Semantic near-match check + a stored embedding for future "
        "search -- one real embedding API call. Set false for a faster write "
        "with exact-name/alias dedup only.",
    )


@router.post("", status_code=201)
async def create_goal_route(
    body: GoalCreateBody,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """ingestion.md Sec 19's user-facing flow: search near matches first.
    Returns one of:
      {"outcome": "near_matches", "candidates": [...]}   -- nothing written
      {"outcome": "matched", "goal": {...}}               -- an identical
        goal already existed (exact name/alias/near-identical embedding)
      {"outcome": "created", "goal": {...}}               -- a genuinely
        new, `status='candidate'` goal, owned by the authenticated caller

    Embedding step mirrors POST /v1/procedures' own privacy discipline:
    a caller-submitted goal description is USER_PRIVATE text, gated on
    ProviderPolicyService before any external embedding call -- a denial
    just means no embedding-based near-match check / stored vector, never
    a failed creation.
    """
    embedder = None
    if body.use_embeddings:
        from app.services.classification import DataClass
        from app.services.embeddings import Embedder

        embedder = Embedder(data_classification=DataClass.USER_PRIVATE, policy_pool=pool)

    async def _create(with_embedder):
        return await create_goal_from_user(
            pool, canonical_name=body.canonical_name, description=body.description,
            objective=body.objective, constraints=body.constraints,
            scope_type=body.scope_type, scope_entity_id=body.scope_entity_id,
            owner_id=_owner_key(principal), embedder=with_embedder,
            allow_create_anyway=body.allow_create_anyway,
        )

    try:
        try:
            result = await _create(embedder)
        except Exception as exc:  # noqa: BLE001
            # A provider-policy denial (or any other embedding-path
            # failure) must never fail the creation outright -- same
            # "best-effort embedding, never blocks the write" discipline
            # POST /v1/procedures already applies. Retry once, with no
            # embedder at all (exact-name/alias dedup only).
            from app.services.provider_policy import ProviderPolicyDenied

            if embedder is None or not isinstance(exc, ProviderPolicyDenied):
                raise
            result = await _create(None)
    except V0Violation as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@router.get("/{goal_id}/solutions")
async def goal_solutions(goal_id: str, pool=Depends(get_pool),
                         scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    # B1 hardening, defense in depth: the public Goal page reads this
    # endpoint directly. Even though the association API can no longer
    # itself create a 'proposed' row that later gets silently promoted,
    # this filter means an unreviewed association could NEVER surface
    # here even if some other internal writer ever created one at a
    # status other than 'active' -- 'active' is set in exactly one place,
    # app.economy.submissions.review_procedure_submission/
    # review_benchmark_submission, once a human has accepted the submission.
    all_solutions = await pm.list_goal_solutions(pool, goal_id, scope=scope)
    return {"solutions": [s for s in all_solutions if s.get("status") == "active"]}


@router.get("/{goal_id}/benchmarks")
async def goal_benchmarks(goal_id: str, pool=Depends(get_pool),
                          scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"benchmarks": await pm.list_goal_benchmarks(pool, goal_id, scope=scope)}


@router.get("/{goal_id}/evaluations")
async def goal_evaluations(goal_id: str, pool=Depends(get_pool),
                           scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return {"evaluations": await pm.list_goal_evaluations(pool, goal_id, scope=scope)}


@router.get("/{goal_id}/leaderboard")
async def goal_leaderboard_route(goal_id: str, benchmark_id: Optional[str] = None,
                                 pool=Depends(get_pool),
                                 scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    if await pm.get_goal_for_product(pool, goal_id, scope=scope) is None:
        raise HTTPException(status_code=404, detail="goal not found or out of scope")
    return await pm.goal_leaderboard(pool, goal_id, scope=scope, benchmark_id=benchmark_id)


# ---------------------------------------------------------------------------
# Best verified solution (goal-agnostic path -- takes NL text, not an id)
# ---------------------------------------------------------------------------
_best_way_router = APIRouter(tags=["goals"])


@_best_way_router.get("/v1/best-way")
async def best_verified_solution(goal: str, pool=Depends(get_pool),
                                 scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    return await pm.find_best_verified_solution(pool, goal, scope=scope)


# ---------------------------------------------------------------------------
# Benchmark / Solution / Evaluation -- top-level resources, not nested
# under /goals/{goal_id} since they're addressed by their own id.
# ---------------------------------------------------------------------------
_products_router = APIRouter(prefix="/v1", tags=["goals"])


class BenchmarkIn(BaseModel):
    goal_id: str
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
    goal_id: str
    solution_type: str
    target_id: str
    version: int = 1
    status: str = "proposed"
    proposer: Optional[str] = None
    provenance: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationIn(BaseModel):
    goal_id: str
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


@_products_router.post("/benchmarks", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
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
# directly via POST /v1/benchmarks (still just a 'draft' row, unchanged)
# has no such row and can never be frozen through this route, and (3)
# audited: actor/target/reason recorded via the same record_audit_event
# ticket-09 private-object-creation already uses.
@_products_router.post("/benchmarks/{benchmark_id}/freeze", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_PUBLISH))])
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


@_products_router.get("/benchmarks/{benchmark_id}")
async def get_benchmark(benchmark_id: str, pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    b = await pm.get_benchmark(pool, benchmark_id, scope=scope)
    if b is None:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return b


# B1 hardening (audit finding: this endpoint was a second, unreviewed path
# to a "live" association -- a caller could set status='active' and an
# arbitrary `proposer` directly, with no relationship at all to
# app.economy.submissions's procedure_submissions/benchmark_submissions
# review workflow). This is now the ONE public entry point for a
# client-initiated association, and it can only ever create a 'proposed'
# one: identity is server-derived, and status is forced regardless of
# what the body asks for. It is NOT a second path to 'active' -- the only
# way a solution reaches 'active' (and therefore appears on a Goal page /
# is ranked -- see goal_solutions's status filter above) is
# app.economy.submissions.review_procedure_submission/
# review_benchmark_submission calling app.services.product_model.
# associate_solution DIRECTLY (a Python call, not this HTTP route) once a
# human has accepted the submission.
@_products_router.post("/solutions/associate", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def associate_solution(
    body: SolutionIn, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    # Never let a public call downgrade an association the canonical
    # review workflow already made 'active' -- the ON CONFLICT upsert
    # below would otherwise let anyone "un-list" an accepted Procedure/
    # Benchmark just by re-posting the same association.
    existing = await pool.fetchrow(
        "SELECT * FROM solutions WHERE goal_id=$1 AND solution_type=$2 AND target_id=$3 AND version=$4",
        body.goal_id, body.solution_type, body.target_id, body.version,
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


@_products_router.post("/evaluations", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def request_evaluation(body: EvaluationIn, pool=Depends(get_pool),
                             scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.request_evaluation(pool, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@_products_router.post("/evaluations/{evaluation_id}/complete", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
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


@_products_router.post("/evaluations/{evaluation_id}/invalidate", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def invalidate_evaluation(evaluation_id: str, reason: str = Query(...),
                                pool=Depends(get_pool),
                                scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await pm.invalidate_evaluation(pool, evaluation_id, reason=reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@_products_router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, pool=Depends(get_pool),
                         scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    e = await pm.get_evaluation(pool, evaluation_id, scope=scope)
    if e is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return e
