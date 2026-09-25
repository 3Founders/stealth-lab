"""Reviewer queue for the Goal hierarchy (remaining_work.md #10).

Reviewers (scope `knowledge:publish`) see proposed SPECIALIZES edges and Goals
placement flagged (`orphan` / `uncertain`), and decide proposed edges. A decision
can only move a PROPOSED edge to accepted/rejected -- through the same graph
checks every other writer uses; clients can never create an accepted edge here."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, require_authenticated_user, require_scopes
from app.services import auth_context as _ac
from app.services import goal_review as review
from app.services.goal_abstraction import (
    GoalRelationCycleError,
    GoalRelationRedundancyError,
    GoalRelationScopeError,
    GoalRelationSelfError,
    GoalRelationStatusConflict,
    GoalRelationVisibilityError,
)


async def get_pool(request: Request):
    return request.app.state.pool


router = APIRouter(prefix="/v1/goal-review", tags=["goal-review"],
                   dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_PUBLISH))])


class RelationDecision(BaseModel):
    specific_goal_id: str
    abstract_goal_id: str
    decision: Literal["accept", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


class ItemClose(BaseModel):
    status: Literal["resolved", "dismissed"]


@router.get("/relations")
async def list_proposed_relations(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    pool=Depends(get_pool), principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    items, has_more = await review.list_proposed_relations(
        pool, access_scope=principal.access_scope(), limit=limit, offset=offset)
    return {"items": items, "has_more": has_more}


@router.post("/relations/decide")
async def decide_proposed_relation(
    body: RelationDecision, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    try:
        result = await review.decide_proposed_relation(
            pool, specific_goal_id=body.specific_goal_id, abstract_goal_id=body.abstract_goal_id,
            decision=body.decision, reviewer=principal.subject, reason=body.reason,
            access_scope=principal.access_scope())
    except GoalRelationStatusConflict as exc:
        raise HTTPException(status_code=409, detail=f"not a proposed relation any more: {exc}") from exc
    except (GoalRelationCycleError, GoalRelationRedundancyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (GoalRelationVisibilityError, GoalRelationScopeError, GoalRelationSelfError) as exc:
        raise HTTPException(status_code=404, detail="relation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"relation": {k: result.get(k) for k in ("specific_goal_id", "abstract_goal_id", "status", "decided_by")
                         if k in result} or result}


@router.get("/items")
async def list_review_items(
    status: Literal["open", "resolved", "dismissed"] = "open",
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    pool=Depends(get_pool), principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    items, has_more = await review.list_review_items(
        pool, access_scope=principal.access_scope(), status=status, limit=limit, offset=offset)
    return {"items": items, "has_more": has_more}


@router.post("/items/{item_id}/close")
async def close_review_item(
    item_id: str, body: ItemClose, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    if not await review.close_review_item(pool, item_id=item_id, reviewer=principal.subject, status=body.status):
        raise HTTPException(status_code=404, detail="open review item not found")
    return {"id": item_id, "status": body.status}
