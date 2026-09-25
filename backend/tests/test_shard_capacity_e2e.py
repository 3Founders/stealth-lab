"""Storage guard + retention against real databases.

Capacity: a remote shard over its byte limit is marked `full`, after which new
public Goals are placed elsewhere and the old ones stay readable. Uses the
control/shard fixture of test_sharded_writes_e2e (needs TEST_SHARD_DATABASE_URL).
Retention: only finished operational rows past the age are pruned, and only on apply."""
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import shards as sh
from app.services.retention import prune_operational_rows
from app.services.shard_capacity import enforce_shard_capacity
from tests.test_sharded_writes_e2e import SHARD_DSN, env, goal  # noqa: F401 -- `env` is the fixture

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not (DATABASE_URL and SHARD_DSN), reason="needs DATABASE_URL + TEST_SHARD_DATABASE_URL")
async def test_a_shard_over_its_byte_limit_stops_taking_new_goals(env):
    pool, shard, _ = env
    before = await goal(pool, "placed before the shard filled up")
    assert before["home_shard_id"] == "K001"

    # a 1-byte limit: any real database is over it
    await pool.execute("UPDATE knowledge_shards SET capacity_bytes = 1 WHERE shard_id = 'K001'")
    await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
    sh.invalidate_shard_cache()

    dry = await enforce_shard_capacity(pool, apply=False)
    assert next(r for r in dry if r["shard_id"] == "K001")["action"] == "would_mark_full"
    assert (await pool.fetchval("SELECT status FROM knowledge_shards WHERE shard_id = 'K001'")) == "active"

    report = await enforce_shard_capacity(pool, apply=True)
    k001 = next(r for r in report if r["shard_id"] == "K001")
    assert k001["action"] == "marked_full" and k001["used_ratio"] > 1
    assert (await pool.fetchval("SELECT status FROM knowledge_shards WHERE shard_id = 'K001'")) == "full"

    after = await goal(pool, "placed after the shard filled up")
    assert after["home_shard_id"] == "K000"                                    # new objects roll over
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE id = $1::uuid", before["id"]) == 1   # old ones stay
    await pool.execute("UPDATE knowledge_shards SET capacity_bytes = NULL, status = 'active' WHERE shard_id = 'K001'")


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
async def test_retention_prunes_only_finished_old_operational_rows(pool):
    marker = f"retention-{uuid.uuid4().hex[:8]}"
    old_id = await pool.fetchval(
        "INSERT INTO retrieval_decisions (query_sha256, mode, created_at) VALUES ($1, 'test', now() - interval '40 days') RETURNING id",
        marker)
    new_id = await pool.fetchval(
        "INSERT INTO retrieval_decisions (query_sha256, mode) VALUES ($1, 'test') RETURNING id", marker)

    counted = await prune_operational_rows(pool, older_than_days=30, apply=False)
    assert counted["tables"]["retrieval_decisions"] >= 1
    assert await pool.fetchval("SELECT count(*) FROM retrieval_decisions WHERE id = $1", old_id) == 1   # dry run

    await prune_operational_rows(pool, older_than_days=30, apply=True)
    assert await pool.fetchval("SELECT count(*) FROM retrieval_decisions WHERE id = $1", old_id) == 0
    assert await pool.fetchval("SELECT count(*) FROM retrieval_decisions WHERE id = $1", new_id) == 1
    await pool.execute("DELETE FROM retrieval_decisions WHERE query_sha256 = $1", marker)
