"""
/v1/contributors -- the public people surface (search, leaderboard, profile).

Read-only. No authentication required; `optional_authenticated_user` is taken
only so a signed-in viewer could later get richer results without a second
endpoint. Only profiles their owner has explicitly set to 'public' are ever
returned. A missing or non-public profile is a 404 that does not confirm the
account exists (INV-01: private by default; nothing about a person is
world-readable until they opt in).

The contributor leaderboard ranks people; it is unrelated to
/v1/problems/{id}/leaderboard, which ranks solutions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import optional_authenticated_user  # noqa: F401  (reserved for viewer enrichment)
from app.services import contributors

router = APIRouter(prefix="/v1/contributors", tags=["contributors"])


async def get_pool(request: Request):
    return request.app.state.pool


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


@router.get("/{user_id}")
async def get_contributor(user_id: str, pool=Depends(get_pool)) -> dict:
    profile = await contributors.public_profile(pool, user_id)
    if profile is None:
        raise HTTPException(404, "no public contributor profile for this id")
    return profile
