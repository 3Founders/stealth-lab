"""
/v1/me/profile -- the caller's own contributor profile (opt-in public listing)
and V1 contributor identity: keळ username + avatar + onboarding state.

GET  lazily provisions the profile row + a generated username on first call
     (contributors.ensure_profile) and returns it alongside live contribution
     counts, the public-listing disclosure state, and onboarding state --
     enough for the UI to route to /onboarding or straight into the app.
PUT  accepts visibility/tagline (unchanged behaviour -- one audited
     'profile_visibility_changed' event per call that includes them),
     username (explicit rename, validated + reserved server-side), and
     onboarding_complete. Any subset may be sent in one call.
POST/DELETE /avatar upload or remove the caller's avatar image, reusing the
     existing ObjectStore abstraction (app/services/object_storage.py) --
     no second storage path, no raw bytes in Postgres.

Identity is the validated principal only -- no request-body identity field is
consulted (same rule as app/api/me.py and app/api/deps.py::get_scope).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, require_authenticated_user
from app.services import auth_context as _ac
from app.services import avatar as avatar_service
from app.services import contributors
from app.services import object_storage
from app.services.audit import record_audit_event
from app.services.governance import RateLimitExceeded, RateLimiter
from app.services.username_generator import InvalidUsername

router = APIRouter(prefix="/v1/me/profile", tags=["me"])
avatar_router = APIRouter(prefix="/v1/me/avatar", tags=["me"])


async def get_pool(request: Request):
    return request.app.state.pool


class ProfileUpdate(BaseModel):
    visibility: Optional[str] = Field(default=None, pattern="^(private|public)$")
    tagline: Optional[str] = Field(default=None, max_length=280)
    username: Optional[str] = Field(default=None, min_length=1, max_length=32)
    onboarding_complete: Optional[bool] = None


def _avatar_url(profile: Optional[dict]) -> Optional[str]:
    username = (profile or {}).get("username")
    return f"/v1/contributors/by-username/{username}/avatar" if username else None


@router.get("")
async def get_my_profile(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    profile = await contributors.ensure_profile(pool, principal.user_id)
    counts = await contributors.contribution_counts(
        pool, user_id=principal.user_id, subject=principal.subject
    )
    return {
        "profile": profile,
        "counts": counts,
        "display_name": principal.name,
        "avatar_url": _avatar_url(profile),
        "disclosure_required": profile.get("disclosed_at") is None,
        "onboarding_required": not profile.get("onboarding_complete"),
        # UI-display convenience ONLY -- every route that actually accepts
        # or rejects a submission independently re-checks KNOWLEDGE_PUBLISH
        # via require_scopes (app/api/economy.py), same rule as every other
        # scope-gated UI affordance in this codebase.
        "is_reviewer": _ac.KNOWLEDGE_PUBLISH in principal.scopes,
    }


@router.get("/username/suggestions")
async def suggest_usernames(
    request: Request,
    limit: int = 1,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Read-only 'Generate another' -- returns candidate name(s), reserves
    nothing. Rate-limited (see governance.DEFAULT_LIMITS) so it stays a
    quick onboarding pick, not a namespace scan."""
    try:
        await RateLimiter(pool).check_and_record(principal.rate_key, request.url.path)
    except RateLimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}) from exc
    limit = max(1, min(int(limit), 5))
    return {"suggestions": await contributors.suggest_username(pool, limit=limit)}


@router.put("")
async def set_my_profile(
    body: ProfileUpdate,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    profile: Optional[dict] = None

    if body.visibility is not None:
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

    if body.username is not None:
        try:
            profile = await contributors.rename_username(pool, principal.user_id, body.username)
        except InvalidUsername as exc:
            raise HTTPException(422, exc.reason) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await record_audit_event(
            pool, actor_subject=principal.subject, actor_user_id=principal.user_id,
            action="username_changed", object_type="contributor_profile", object_id=str(principal.user_id),
        )

    if body.onboarding_complete:
        profile = await contributors.complete_onboarding(pool, principal.user_id)

    if profile is None:
        profile = await contributors.ensure_profile(pool, principal.user_id)

    return {
        "profile": profile,
        "avatar_url": _avatar_url(profile),
        "counts": await contributors.contribution_counts(
            pool, user_id=principal.user_id, subject=principal.subject
        ),
    }


@avatar_router.post("")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    try:
        await RateLimiter(pool).check_and_record(principal.rate_key, request.url.path)
    except RateLimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}) from exc

    data = await file.read(avatar_service.MAX_UPLOAD_BYTES + 1)
    if len(data) > avatar_service.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"avatar must be under {avatar_service.MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    try:
        processed = avatar_service.process_avatar(data)
    except avatar_service.InvalidImage as exc:
        raise HTTPException(422, str(exc)) from exc

    store = object_storage.get_store()
    if store is None:
        raise HTTPException(503, "object storage is not configured for this deployment")

    await contributors.ensure_profile(pool, principal.user_id)  # username must exist before an avatar can attach to it
    _sha, locator = await store.put(processed, content_type="image/webp")
    profile = await contributors.set_avatar(pool, principal.user_id, locator)
    await record_audit_event(
        pool, actor_subject=principal.subject, actor_user_id=principal.user_id,
        action="avatar_updated", object_type="contributor_profile", object_id=str(principal.user_id),
    )
    return {"profile": profile, "avatar_url": _avatar_url(profile)}


@avatar_router.delete("")
async def remove_avatar(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    await contributors.ensure_profile(pool, principal.user_id)
    profile = await contributors.set_avatar(pool, principal.user_id, None)
    await record_audit_event(
        pool, actor_subject=principal.subject, actor_user_id=principal.user_id,
        action="avatar_removed", object_type="contributor_profile", object_id=str(principal.user_id),
    )
    return {"profile": profile, "avatar_url": None}
