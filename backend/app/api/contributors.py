"""
/v1/contributors -- the public people surface (search, leaderboard, profile).

Read-only. No authentication required; `optional_authenticated_user` is taken
only so a signed-in viewer could later get richer results without a second
endpoint. Only profiles their owner has explicitly set to 'public' are ever
returned. A missing or non-public profile is a 404 that does not confirm the
account exists (INV-01: private by default; nothing about a person is
world-readable until they opt in).

The contributor leaderboard ranks people; it is unrelated to
/v1/goals/{id}/leaderboard, which ranks solutions.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import optional_authenticated_user
from app.services import avatar as avatar_service
from app.services import contributors
from app.services import object_storage

router = APIRouter(prefix="/v1/contributors", tags=["contributors"])


async def get_pool(request: Request):
    return request.app.state.pool


def _public_view(row: dict) -> dict:
    """Strip the internal `users.id` (migration 28) before it ever reaches a
    public response -- the /{user_id} path keeps it (the caller already
    supplied it in the URL; see that route's own docstring), but every
    NEW public surface added for the V1 identity pass must never echo it
    back. Ownership stays anchored to that id internally; nothing public
    is keyed on it."""
    out = dict(row)
    out.pop("user_id", None)
    return out


# Static paths are declared before "/{user_id}" so they are matched first.


@router.get("/search")
async def search_contributors(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    pool=Depends(get_pool),
) -> dict:
    results = await contributors.search_public(pool, q, limit=limit)
    return {"query": q, "results": results}


@router.get("/leaderboard")
async def contributor_leaderboard(
    metric: str = Query(contributors.LEADERBOARD_METRICS[0]),
    limit: int = Query(25, ge=1, le=100),
    pool=Depends(get_pool),
) -> dict:
    try:
        return await contributors.leaderboard(pool, metric=metric, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/by-username/{username}")
async def get_contributor_by_username(
    username: str,
    pool=Depends(get_pool),
    principal: Optional[object] = Depends(optional_authenticated_user),
) -> dict:
    """Public profile by keळ username (the frontend's /u/[username]).
    Resolves the CURRENT username first, then username_history, so an old
    link keeps landing on the right account -- never a different person's
    (contributors.get_profile_by_username never resolves a name to more
    than one owner, current or historical, by construction of migration
    106's unique indexes + reject_reused_username trigger).

    `public_profile` -- not this route -- is the one place that decides
    "is this visible publicly": a real username whose profile is private
    reports 404, identically to an unknown username, so a 404 here never
    confirms an account exists to anyone else. The one exception is the
    OWNER themselves (matches the avatar route's own owner-exception,
    below): a private profile is still yours to preview, so a signed-in
    caller viewing their own username gets it back with `is_owner: true`
    and the real `visibility`, instead of the generic 404 -- the frontend
    uses that to show "this is private" instead of pretending the account
    doesn't exist to the one person who already knows it does."""
    resolved = await contributors.get_profile_by_username(pool, username)
    if resolved is None:
        raise HTTPException(404, "no public contributor profile for this username")
    owner_id = str(resolved["user_id"])

    is_owner = principal is not None and getattr(principal, "user_id", None) is not None \
        and str(principal.user_id) == owner_id
    if is_owner:
        own = await contributors.get_profile(pool, owner_id)
        counts = await contributors.contribution_counts(pool, user_id=owner_id, subject=principal.subject)
        profile = {
            "username": own["username"],
            "display_name": principal.name or "Contributor",
            "tagline": own["tagline"],
            "profile_since": own["t_created"],
            "counts": counts,
            "visibility": own["visibility"],
            "is_owner": True,
        }
    else:
        profile = await contributors.public_profile(pool, owner_id)
        if profile is None:
            raise HTTPException(404, "no public contributor profile for this username")
        profile["is_owner"] = False

    # Set only when `username` was an OLD name for this same account -- the
    # frontend uses this to replace the URL with the current one, so old
    # bookmarks/links settle onto /u/<current-username> instead of staying
    # on a retired name forever.
    profile["renamed_to"] = resolved.get("renamed_to")
    return _public_view(profile)


@router.get("/by-username/{username}/avatar")
async def get_contributor_avatar(
    username: str,
    pool=Depends(get_pool),
    principal=Depends(optional_authenticated_user),
) -> Response:
    """Avatar delivery (V1 identity spec §18). Object storage is not assumed
    publicly readable -- this is the one sanctioned public-serving path, so
    a bucket/endpoint never needs its own public ACL. Eligible callers:
    anyone, for a PUBLIC profile; the account owner, always (so your own
    header/settings can show your avatar even while your profile is
    private). Everyone else gets the same 404 a missing username would --
    consistent with public_profile's own privacy contract."""
    resolved = await contributors.get_profile_by_username(pool, username)
    if resolved is None:
        raise HTTPException(404, "no such profile")

    is_owner = principal is not None and getattr(principal, "user_id", None) is not None \
        and str(principal.user_id) == str(resolved["user_id"])
    if not is_owner:
        public = await contributors.public_profile(pool, str(resolved["user_id"]))
        if public is None:
            raise HTTPException(404, "no such profile")

    locator = resolved.get("avatar_locator")
    if locator:
        store = object_storage.get_store()
        data: Optional[bytes] = None
        if store is not None:
            try:
                data = await store.get(locator)
            except object_storage.ObjectStoreCorrupt:
                data = None
        if data is not None:
            # Content-addressed: a replace always writes a NEW locator
            # (contributors.set_avatar's docstring), so this exact URL's
            # bytes never change -- safe to cache as immutable.
            return Response(
                content=data, media_type="image/webp",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

    svg = avatar_service.fallback_avatar_svg(
        resolved.get("username") or username, seed=str(resolved["user_id"])
    )
    return Response(
        content=svg, media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{user_id}")
async def get_contributor(user_id: str, pool=Depends(get_pool)) -> dict:
    profile = await contributors.public_profile(pool, user_id)
    if profile is None:
        raise HTTPException(404, "no public contributor profile for this id")
    return profile
