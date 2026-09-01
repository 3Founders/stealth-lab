"""
Read-only Claim Graph API (directive §40).

Thin REST wrapper over `app.services.claim_graph_api` -- mirrors
`app/api/graph.py`'s own structure exactly: `pool` via
`request.app.state.pool`, `scope: AccessScope = Depends(get_scope)`, and
the same anti-enumeration posture (a claim/edge/evidence/procedure row
the caller's scope cannot see is OMITTED from the response body, never
labelled "?" or surfaced as a per-row 404 -- that would leak existence).
The top-level claim itself is the one exception: a claim that does not
resolve, or resolves but isn't visible to `scope`, 404s the whole
request -- same posture `graph.py::get_subgraph` takes for its own
center node.

No business logic lives here. Every handler is a direct call into
`claim_graph_api`'s already-scoped functions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import get_scope
from app.services import claim_graph_api
from app.services.access import AccessScope
from app.services.claims import ALL_CLAIM_RELATIONS

router = APIRouter(prefix="/v1/claims", tags=["claims"])


async def get_pool(request: Request):
    return request.app.state.pool


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ClaimOut(BaseModel):
    id: UUID
    node_type: str
    name: str
    properties: dict[str, Any]
    t_valid: datetime
    t_invalid: Optional[datetime] = None
    created_by: Optional[str] = None
    scope_type: Optional[str] = None
    scope_entity_id: Optional[str] = None


class ClaimRelationOut(BaseModel):
    id: UUID
    source_id: UUID
    target_id: UUID
    relation: str
    created_by: Optional[str] = None
    t_valid: Optional[datetime] = None
    properties: dict[str, Any] = {}


class RelationHopOut(BaseModel):
    edge_id: str
    relation: str
    hop: int
    from_claim_id: str
    to_claim_id: str
    properties: dict[str, Any] = {}


class TraverseResponse(BaseModel):
    claim_id: str
    supporting: list[RelationHopOut]
    contradicting: list[RelationHopOut]
    other: list[RelationHopOut]


class EvidenceOut(BaseModel):
    id: UUID
    evidence_type: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[UUID] = None
    outcome_status: Optional[str] = None
    direction: Optional[str] = None
    strength_score: Optional[float] = None
    failure_class: Optional[str] = None
    created_by: Optional[str] = None
    t_valid: Optional[datetime] = None


class DependentProcedureOut(BaseModel):
    id: UUID
    name: str


class ClaimVersionOut(BaseModel):
    claim_id: str
    version: int
    statement: Optional[str] = None
    t_valid: Optional[datetime] = None
    commits: list[dict[str, Any]] = []
    properties: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _require_visible_claim(pool, claim_id: UUID, *, scope: AccessScope) -> dict:
    claim = await claim_graph_api.get_claim(pool, str(claim_id), scope=scope)
    if claim is None:
        raise HTTPException(404, "claim not found")
    return claim


@router.get("/{claim_id}", response_model=ClaimOut)
async def get_claim(
    claim_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> ClaimOut:
    claim = await _require_visible_claim(pool, claim_id, scope=scope)
    return ClaimOut(**claim)


@router.get("/{claim_id}/neighbors", response_model=list[ClaimRelationOut])
async def get_claim_neighbors(
    claim_id: UUID,
    direction: Literal["outgoing", "incoming", "both"] = Query(default="both"),
    relation: Optional[list[str]] = Query(
        default=None,
        description="Restrict to these relation names; repeat for multiple. Defaults to every known claim relation.",
    ),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[ClaimRelationOut]:
    await _require_visible_claim(pool, claim_id, scope=scope)
    relations = set(relation) if relation else None
    if relations is not None:
        unknown = relations - ALL_CLAIM_RELATIONS
        if unknown:
            raise HTTPException(422, f"unknown relation(s): {sorted(unknown)}")
    rows = await claim_graph_api.get_claim_neighbors(
        pool, str(claim_id), relations=relations, direction=direction, scope=scope,
    )
    return [ClaimRelationOut(**row) for row in rows]


@router.get("/{claim_id}/traverse", response_model=TraverseResponse)
async def traverse_claim(
    claim_id: UUID,
    max_hops: int = Query(default=2, ge=1, le=2),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> TraverseResponse:
    await _require_visible_claim(pool, claim_id, scope=scope)
    result = await claim_graph_api.traverse_claim_graph(
        pool, str(claim_id), max_hops=max_hops, scope=scope,
    )
    return TraverseResponse(**result)


@router.get("/{claim_id}/evidence", response_model=list[EvidenceOut])
async def get_claim_evidence(
    claim_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[EvidenceOut]:
    await _require_visible_claim(pool, claim_id, scope=scope)
    rows = await claim_graph_api.get_claim_evidence_api(pool, str(claim_id), scope=scope)
    return [EvidenceOut(**row) for row in rows]


@router.get("/{claim_id}/dependents", response_model=list[DependentProcedureOut])
async def get_claim_dependents(
    claim_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[DependentProcedureOut]:
    await _require_visible_claim(pool, claim_id, scope=scope)
    rows = await claim_graph_api.get_claim_dependents(pool, str(claim_id), scope=scope)
    return [DependentProcedureOut(**row) for row in rows]


@router.get("/{claim_id}/history", response_model=list[ClaimVersionOut])
async def get_claim_history(
    claim_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[ClaimVersionOut]:
    await _require_visible_claim(pool, claim_id, scope=scope)
    rows = await claim_graph_api.get_claim_history(pool, str(claim_id), scope=scope)
    return [ClaimVersionOut(**row) for row in rows]
