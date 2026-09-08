"""
/v1/me/profile -- the caller's own contributor profile (opt-in public listing).

GET  returns the profile row (or null when never created), the caller's live
     contribution counts, and whether the public-listing disclosure still
     needs to be acknowledged -- enough for the UI to decide whether to show
     the disclosure screen.
PUT  sets visibility ('private' | 'public') and an optional tagline. Every
     call stamps the disclosure acknowledgement (disclosed_at) and emits an
     audited 'profile_visibility_changed' event through the ONE audit writer.

Identity is the validated principal only -- no request-body identity field is
consulted (same rule as app/api/me.py and app/api/deps.py::get_scope).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, require_authenticated_user
from app.services import contributors
from app.services.audit import record_audit_event

router = APIRouter(prefix="/v1/me/profile", tags=["me"])


async def get_pool(request: Request):
    return request.app.state.pool


class ProfileUpdate(BaseModel):
    visibility: str = Field(pattern="^(private|public)$")
    tagline: Optional[str] = Field(default=None, max_length=280)


@router.get("")
async def get_my_profile(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    profile = await contributors.get_profile(pool, principal.user_id)
    counts = await contributors.contribution_counts(
        pool, user_id=principal.user_id, subject=principal.subject
    )
    return {
        "profile": profile,
        "counts": counts,
        "display_name": principal.name,
        "disclosure_required": profile is None or profile.get("disclosed_at") is None,
    }


@router.put("")
async def set_my_profile(
    body: ProfileUpdate,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    before = await contributors.get_profile(pool, principal.user_id)
    profile = await contributors.upsert_profile(
        pool,
        principal.user_id,
        visibility=body.visibility,
        tagline=body.tagline,
        mark_disclosed=True,
    )
    await record_audit_event(
        pool,
        actor_subject=principal.subject,
        actor_user_id=principal.user_id,
        action="profile_visibility_changed",
        object_type="contributor_profile",
        object_id=str(principal.user_id),
        details={
            "from": (before or {}).get("visibility", "private"),
            "to": body.visibility,
            "tagline_set": bool((body.tagline or "").strip()),
            "first_disclosure": before is None or before.get("disclosed_at") is None,
        },
    )
    return {"profile": profile, "counts": await contributors.contribution_counts(
        pool, user_id=principal.user_id, subject=principal.subject
    )}
