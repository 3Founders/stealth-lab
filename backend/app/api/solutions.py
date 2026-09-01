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

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.procedure_graph_api import get_solution_view

router = APIRouter(prefix="/v1/solutions", tags=["solutions"])


async def get_pool(request: Request):
    return request.app.state.pool


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
