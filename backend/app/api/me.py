"""
Personal contributions API (directive Sec 32.4/34, "/me").

Identity comes from the EXACT SAME mechanism `app/api/deps.py::get_scope`
already resolves for every other endpoint -- a validated Band 2.9 OIDC
actor's `.subject` wins when one exists (`app.services.authn.
current_actor()`, published on a contextvar by the actor middleware);
otherwise the trusted-only-because-nothing-is-private-yet `X-Viewer-Id`
header; otherwise anonymous. No second identity path is introduced here.

An anonymous caller (no validated actor, no header) gets a real 401 --
this endpoint never fabricates a personal contribution history for
someone it cannot identify.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.personal_contributions import get_personal_contributions

router = APIRouter(prefix="/v1/me", tags=["me"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("")
async def get_me(
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    if scope.viewer_id is None:
        raise HTTPException(
            401,
            "no identity for this request -- anonymous callers have no "
            "personal contribution history",
        )
    return await get_personal_contributions(pool, scope.viewer_id, scope=scope)
