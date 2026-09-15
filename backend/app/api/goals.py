"""
Goal REST API (ingestion.md Sec 18-19/25) -- the frontend-usable surface
over app.services.goals, mirroring app/api/procedures.py's own
conventions exactly (get_scope for reads, require_authenticated_user +
privacy-aware Embedder for the one write route). The MCP tools
(search_goals/inspect_goal/create_goal/list_goal_procedures/
list_goal_implementations in app/mcp_server/server.py) wrap the SAME
service functions this router calls -- no duplicated logic, two thin
callers of one real implementation.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user
from app.services.access import AccessScope
from app.services.goals import create_goal_from_user, get_goal, search_goals
from app.services.v0_gate import V0Violation

router = APIRouter(prefix="/v1/goals", tags=["goals"])


async def get_pool(request: Request):
    return request.app.state.pool


def _owner_key(principal: AuthenticatedPrincipal) -> str:
    return principal.subject


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
