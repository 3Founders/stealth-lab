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

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user
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


# --- Phase 6: data rights (LC-006 / LC-007) --------------------------------


@router.get("/export")
async def export_my_data(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Machine-readable export of the caller's own data. Global Commons
    knowledge appears only as publication-action references."""
    from app.services.data_rights import export_user_data

    return await export_user_data(
        pool, subject=principal.subject, actor_user_id=principal.user_id
    )


@router.get("/deletion")
async def preview_my_deletion(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """What a 'delete my data' request would do -- no mutation."""
    from app.services.data_rights import delete_user_data

    return await delete_user_data(pool, subject=principal.subject, dry_run=True)


@router.post("/deletion")
async def request_my_deletion(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Execute deletion: physical-delete private rows with no downstream
    publication, tombstone published sources (history kept, vector cleared),
    retain publication records and independently-sourced global objects.
    Refused if the subject is under legal hold."""
    from app.services.data_rights import delete_user_data

    try:
        return await delete_user_data(
            pool, subject=principal.subject, actor_user_id=principal.user_id,
            dry_run=False,
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
