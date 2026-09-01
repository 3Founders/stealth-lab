"""
Procedure Graph API (directive §41).

Read-only REST surface over `app.services.procedure_graph_api` --
mirrors `app/api/graph.py`'s precedent exactly: `get_scope` resolves the
viewer, every service call carries that scope, and a row the viewer
can't see is a plain 404, not a labelled placeholder (same
anti-enumeration posture `graph.py` already documents).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.execution.procedure_graph import ProcedureCompositionError
from app.services.access import AccessScope
from app.services.domain_search import search_global
from app.services.procedure_graph_api import (
    get_procedure_claims,
    get_procedure_detail,
    get_procedure_evidence,
    get_procedure_graph,
    get_procedure_versions,
)

router = APIRouter(prefix="/v1/procedures", tags=["procedures"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("/search")
async def search_procedures(
    q: str,
    repository_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """
    Directive Sec16 convenience endpoint -- a thin, procedure-only leg of
    `domain_search.search_global` (`object_types=["procedure"]`), NOT a
    second retrieval engine: same cascade+RRF ranking `/v1/search` itself
    uses for its procedure bucket, reused verbatim. Registered before
    `/{procedure_row_id}` for readability (the UUID path convertor on
    that route already rejects the literal segment `search` on its own).
    """
    return await search_global(
        pool, q,
        object_types=["procedure"],
        repository_id=repository_id,
        project_id=project_id,
        limit=limit,
        scope=scope,
    )


@router.get("/{procedure_row_id}")
async def read_procedure(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return detail


@router.get("/{procedure_row_id}/versions")
async def read_procedure_versions(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    """`procedure_row_id` names any one version row of the family --
    versions are looked up by that row's own `procedure_id` (the stable
    cross-version handle), so any live/visible member of the chain is a
    valid entry point. 404 only if THAT row itself is missing/invisible;
    an empty version list for a row that does exist is not possible
    (a row is always at least its own version)."""
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_versions(pool, detail["procedure_id"], scope=scope)


@router.get("/{procedure_row_id}/graph")
async def read_procedure_graph(
    procedure_row_id: UUID,
    depth: int = Query(default=2, ge=1, le=8),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    try:
        graph = await get_procedure_graph(pool, str(procedure_row_id), depth=depth, scope=scope)
    except ProcedureCompositionError as exc:
        raise HTTPException(422, str(exc)) from exc
    if graph is None:
        raise HTTPException(404, "procedure not found")
    return graph


@router.get("/{procedure_row_id}/claims")
async def read_procedure_claims(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_claims(pool, str(procedure_row_id), scope=scope)


@router.get("/{procedure_row_id}/evidence")
async def read_procedure_evidence(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_evidence(pool, str(procedure_row_id), scope=scope)
