"""Live-database proof that a task's registered Goal is followed through merges. Skipped unless DATABASE_URL is local."""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
_LOCAL = bool(DATABASE_URL) and (urlparse(DATABASE_URL).hostname in ("127.0.0.1", "localhost", "::1"))

pytestmark = pytest.mark.skipif(not _LOCAL, reason="needs a LOCAL DATABASE_URL with migration 129 (this test writes)")


def test_a_merged_task_goal_resolves_to_its_survivor():
    from app.db.session import create_pool
    from app.ingest.common.tasks import _registered
    from app.services.goals import find_or_create_goal

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid.uuid4().hex[:8]
        key = f"swe:test-merge-{run_id}"
        try:
            kw = dict(scope_type="global", provenance="system_pending_review", judge_mode="none", status="active")
            survivor = await find_or_create_goal(pool, canonical_name=f"Survivor goal {run_id}", **kw)
            loser = await find_or_create_goal(pool, canonical_name=f"Merged goal {run_id}", **kw)
            await pool.execute("INSERT INTO ingest_task_goals (task_key, goal_id, canonical_name, named_by, source) "
                               "VALUES ($1, $2::uuid, $3, 'test', 'test')", key, loser["id"], loser["canonical_name"])
            assert (await _registered(pool, key)).goal_id == str(loser["id"])
            await pool.execute("UPDATE goals SET status = 'merged', merged_into_id = $2::uuid WHERE id = $1::uuid",
                               loser["id"], survivor["id"])
            got = await _registered(pool, key)
            assert got.goal_id == str(survivor["id"]) and got.canonical_name == survivor["canonical_name"]
            assert await _registered(pool, f"swe:missing-{run_id}") is None
        finally:
            await pool.execute("DELETE FROM ingest_task_goals WHERE task_key = $1", key)
            await pool.close()

    asyncio.run(run())
