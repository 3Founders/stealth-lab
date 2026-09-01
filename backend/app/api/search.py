"""
Global search + recommendation REST surface (directive §37-38).

Thin wrapper, no decision logic of its own -- both handlers are one-line
compositions over `app.services.domain_search`. See that module's
docstring for the full design (object-type scope, claim-retrieval
mechanism, why results are grouped rather than cross-type ranked, and the
deliberate naming collision with `mcp_server/server.py`'s own, unrelated
`find_best_way`).

POST for /recommend, not GET: the request body is a real structured
context object (procedure scope/exclusions narrowing dict, invariant
bindings, scope constraints), not just a query string -- the same
reasoning `app/api/ingest.py` and `app/api/chat.py` already apply to
their own structured-body endpoints in this codebase.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.domain_search import find_best_way, search_global

router = APIRouter(prefix="/v1/search", tags=["search"])


async def get_pool(request: Request):
    return request.app.state.pool


class SearchResponse(BaseModel):
    query: str
    object_types: list[str]
    results: dict[str, list[dict[str, Any]]]
    counts: dict[str, int]


@router.get("", response_model=SearchResponse)
async def search(
    q: str,
    object_types: Optional[str] = None,
    repository_id: Optional[str] = None,
    project_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    limit: int = 20,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> SearchResponse:
    """
    `object_types`: comma-separated subset of `procedure,task,claim`
    (e.g. `?object_types=procedure,claim`). Omitted means all three.
    An unsupported value (e.g. `solution`, `problem`) raises a
    `ValueError` inside `search_global` -- see that function's docstring
    for why those two are not searchable entities today. FastAPI has no
    built-in ValueError->422 translation for a plain raise, so that is
    caught here explicitly and turned into a real 422 (a bad request
    parameter, not a server fault) rather than surfacing as an unhandled
    500.
    """
    types = [t.strip() for t in object_types.split(",") if t.strip()] if object_types else None
    try:
        result = await search_global(
            pool, q,
            object_types=types,
            scope_type=scope_type,
            repository_id=repository_id,
            project_id=project_id,
            limit=limit,
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SearchResponse(**result)


class RecommendRequest(BaseModel):
    goal: str = Field(min_length=1)
    context: Optional[dict[str, Any]] = None
    scope_constraint: Optional[dict[str, Any]] = None
    constraints: Optional[dict[str, Any]] = None


class RecommendResponse(BaseModel):
    goal: str
    recommendation: Optional[dict[str, Any]]
    alternatives: list[dict[str, Any]]
    confidence: str
    reason: str


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(
    body: RecommendRequest,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> RecommendResponse:
    """
    "Best known way to do X" -- see `domain_search.find_best_way`'s
    docstring for the naming collision disclosure with `mcp_server/
    server.py`'s own `find_best_way` (that one compiles+persists a plan;
    this one is a pure read, no plan compilation, no persistence).
    """
    result = await find_best_way(
        pool, body.goal,
        context=body.context,
        scope_constraint=body.scope_constraint,
        constraints=body.constraints,
        scope=scope,
    )
    return RecommendResponse(**result)
