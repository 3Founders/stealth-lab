"""
Project Knowledge endpoint (directive §18/§39) — read-only composition
over `app/services/repository_knowledge.py::get_project_knowledge`. No
new schema, no new query logic here: this router only hydrates request
scope and shapes the response. See that module's docstring for the
documented, honest scope limit on project-level claim aggregation (no
rollup across a project's repositories — no real mechanism for that
exists in the schema today).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_scope
from app.api.repositories import ClaimOut, ProcedureOut
from app.services.access import AccessScope
from app.services.repository_knowledge import get_project_knowledge
from pydantic import BaseModel

router = APIRouter(prefix="/v1/projects", tags=["projects"])


async def get_pool(request: Request):
    return request.app.state.pool


class ProjectKnowledgeResponse(BaseModel):
    project_id: str
    claims: list[ClaimOut]
    relevant_procedures: list[ProcedureOut]
    conflicts: list[ClaimOut]
    confidence_summary: dict


@router.get("/{project_id}", response_model=ProjectKnowledgeResponse)
async def get_project(
    project_id: str,
    focus: Optional[str] = Query(default=None),
    depth: int = Query(default=2, ge=1, le=8),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> ProjectKnowledgeResponse:
    result = await get_project_knowledge(
        pool, project_id, focus=focus, depth=depth, scope=scope,
    )
    return ProjectKnowledgeResponse(**result)
