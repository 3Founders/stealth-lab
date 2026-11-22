"""Reviews on a Way: validation, the published-only rule, and the summary.

Offline: AsyncMock rows, no database. The assertions that matter are the refusals (a rating out of
range, a review of an unpublished Way) and that the list's average covers every visible review, not
just the page returned.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import way_reviews as wr

WAY = str(uuid4())
AUTHOR = str(uuid4())


def pool_for(*, way_status="approved", name="Asha", rows=()):
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        {"status": way_status, "parent": str(uuid4())},
        {"id": "review-1", "author_name": name, "rating": 5, "body": "The deck was usable as-is.",
         "created_at": None},
    ]
    pool.fetchval.return_value = name
    pool.fetch.return_value = list(rows)
    return pool


@pytest.mark.asyncio
async def test_upsert_stores_a_published_way_review():
    pool = pool_for()
    out = await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=5,
                          body="  The deck was usable as-is.  ")
    assert out["id"] == "review-1"
    insert = pool.fetchrow.await_args_list[1].args[0]
    assert "ON CONFLICT (submission_id, author_user_id) DO UPDATE" in insert
    assert "scope" in insert and "source" in insert


@pytest.mark.asyncio
async def test_a_second_review_edits_the_first():
    pool = pool_for()
    await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=4, body="second thoughts")
    insert = pool.fetchrow.await_args_list[1].args[0]
    assert "created_at = now()" in insert          # the upsert refreshes the timestamp


@pytest.mark.asyncio
async def test_rating_must_be_between_one_and_five():
    pool = pool_for()
    for bad in (0, 6, "x", None):
        with pytest.raises(wr.ReviewError):
            await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=bad, body="fine")
    pool.fetchrow.assert_not_called()               # nothing reached storage


@pytest.mark.asyncio
async def test_body_must_say_something():
    pool = pool_for()
    with pytest.raises(wr.ReviewError):
        await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=5, body="  ")
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_an_unpublished_way_cannot_be_reviewed():
    pool = pool_for(way_status="reviewing")
    with pytest.raises(wr.ReviewError) as caught:
        await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=5, body="looks fine")
    assert "not published" in str(caught.value)


@pytest.mark.asyncio
async def test_a_missing_way_is_a_lookup_error():
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    with pytest.raises(LookupError):
        await wr.upsert(pool, submission_id=WAY, author_user_id=AUTHOR, rating=5, body="fine")


@pytest.mark.asyncio
async def test_a_non_uuid_author_is_refused():
    pool = pool_for()
    with pytest.raises(wr.ReviewError):
        await wr.upsert(pool, submission_id=WAY, author_user_id="local-user", rating=5, body="fine")
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_list_reports_the_average_over_every_visible_review():
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"id": "r1", "author_name": "Asha", "rating": 5, "body": "good", "created_at": None,
         "total": 3, "average": 4.3333333},
        {"id": "r2", "author_name": "Bo", "rating": 4, "body": "fine", "created_at": None,
         "total": 3, "average": 4.3333333},
    ]
    out = await wr.list_for(pool, WAY, limit=2)
    assert out["count"] == 3
    assert out["average"] == 4.33
    assert len(out["reviews"]) == 2
    assert "total" not in out["reviews"][0] and "average" not in out["reviews"][0]


@pytest.mark.asyncio
async def test_no_reviews_is_zero_and_no_average():
    pool = AsyncMock()
    pool.fetch.return_value = []
    out = await wr.list_for(pool, WAY)
    assert out == {"reviews": [], "count": 0, "average": None}
