"""The public catalogue pages by offset, newest first.

The homepage and the task page append a page at a time as the reader scrolls, so the offset has to
reach the SQL (a limit-only query would return the same first page forever) and a negative offset has
to be clamped rather than raising.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.submissions import public_catalog


@pytest.mark.asyncio
async def test_offset_reaches_the_query():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": "1"}]
    rows = await public_catalog.list_public(pool, q="refund", limit=12, offset=24)
    sql, term, limit, offset = pool.fetch.await_args.args
    assert "OFFSET $3" in sql
    assert term == "refund"
    assert limit == 12
    assert offset == 24
    assert rows == [{"id": "1"}]


@pytest.mark.asyncio
async def test_a_negative_offset_is_clamped_to_zero():
    pool = AsyncMock()
    pool.fetch.return_value = []
    await public_catalog.list_public(pool, offset=-5)
    assert pool.fetch.await_args.args[3] == 0


@pytest.mark.asyncio
async def test_dates_are_serialisable_strings():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": "1", "created_at": datetime(2026, 11, 22, tzinfo=timezone.utc)}]
    rows = await public_catalog.list_public(pool)
    assert rows[0]["created_at"] == "2026-11-22T00:00:00+00:00"
