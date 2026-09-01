"""
Repository Knowledge endpoint (directive §18/§39) — read-only composition
over `app/services/repository_knowledge.py`. No new schema, no new query
logic here: this router only hydrates request scope and shapes the
response.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.repository_knowledge import get_repository_knowledge

router = APIRouter(prefix="/v1/repositories", tags=["repositories"])


async def get_pool(request: Request):
    return request.app.state.pool


class ClaimOut(BaseModel):
    id: str
    name: str
    statement: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    truth_state: str
    confidence: Optional[float] = None
    lifecycle_state: Optional[str] = None
    t_valid: Optional[str] = None


class ProcedureOut(BaseModel):
    id: str
    procedure_id: str
    name: str
    goal: str
    version: int
    verification_state: str
    staleness: str
    availability: str
    approval_status: Optional[str] = None


class RepositoryKnowledgeResponse(BaseModel):
    repository_id: str
    claims: list[ClaimOut]
    relevant_procedures: list[ProcedureOut]
    conflicts: list[ClaimOut]
    confidence_summary: dict


@router.get("/{repository_id}", response_model=RepositoryKnowledgeResponse)
async def get_repository(
    repository_id: str,
    focus: Optional[str] = Query(default=None),
    depth: int = Query(default=2, ge=1, le=8),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> RepositoryKnowledgeResponse:
    result = await get_repository_knowledge(
        pool, repository_id, focus=focus, depth=depth, scope=scope,
    )
    return RepositoryKnowledgeResponse(**result)
