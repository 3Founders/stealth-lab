"""
Solution read-composition (directive §32.5).

The directive explicitly forbids a new `solutions` table: "model it as a
read composition over existing procedures+implementations+evidence."
There is therefore no independent Solution identity anywhere in this
schema -- in this v1, ONE Solution == one procedure+implementation
pairing, ADDRESSED BY THE PROCEDURE'S OWN ROW ID. A future Solution
identity (if one is ever needed -- e.g. to distinguish two DIFFERENT
implementations of the same procedure as two different "solutions") is
real, separate schema work, not something this endpoint pretends to
already have.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.procedure_graph_api import get_solution_view
from app.services.solution_implementations import get_solution_implementation_detail
from app.services.solution_search import search_solutions

router = APIRouter(prefix="/v1/solutions", tags=["solutions"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("/search")
async def read_solutions_search(
    q: str,
    project_id: Optional[str] = Query(default=None),
    repository_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """
    Phase 7 "Google for how to do something" -- ONE blended, ranked list
    of procedure + task hits. Thin wrapper over
    `app.services.solution_search.search_solutions`; see that module's
    docstring for the full interleaving design (round-robin by rank
    position, never a fabricated cross-type score -- CLAUDE.md's RRF/
    applicability separation rule). Registered BEFORE
    `/{procedure_row_id}` in this file so the literal path segment
    `search` is never mistaken for a UUID (FastAPI's uuid path convertor
    already rejects a non-UUID segment and tries the next route either
    way; declared first here for readability, not because it is load-
    bearing).
    """
    return await search_solutions(
        pool, q,
        scope=scope,
        project_id=project_id,
        repository_id=repository_id,
        limit=limit,
    )


@router.get("/{procedure_row_id}")
async def read_solution(
    procedure_row_id: UUID,
    implementation_id: Optional[UUID] = Query(default=None),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    solution = await get_solution_view(
        pool, str(procedure_row_id),
        implementation_id=str(implementation_id) if implementation_id else None,
        scope=scope,
    )
    if solution is None:
        raise HTTPException(404, "solution not found")
    return solution


@router.get("/{procedure_row_id}/implementations")
async def read_solution_implementation_detail(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    """
    Directive Sec 53: detailed, durable implementation descriptors for
    this Solution, ADDITIVE to `GET /{procedure_row_id}`'s existing
    KIND-level `implementations` field -- see
    `app.services.solution_implementations` for why this is a separate
    endpoint rather than an edit to `get_solution_view`'s own payload.
    """
    detail = await get_solution_implementation_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "solution not found")
    return detail
