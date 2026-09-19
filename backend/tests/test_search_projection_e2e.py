"""Live-DB tests: routing trigger, outbox, projection, replay/repair, remote shard."""
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import search_projection as sp
from app.services.shards import HOME_SHARD, ShardPools, register_shard

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
TAG = "projtest"


async def _mk_goal(pool, name, *, shard=HOME_SHARD, desc=None):
    gid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO goals (id, canonical_name, normalized_name, description, status, scope_type, home_shard_id) "
        "VALUES ($1::uuid, $2, $3, $4, 'active', 'global', $5)", gid, name, name.lower(), desc, shard)
    return gid


async def _mk_proc(pool, goal_id, name, goal_text):
    row = await pool.fetchrow(
        "INSERT INTO procedures (name, goal, steps, scope_type, achieves_goal_id) "
        "VALUES ($1, $2, '[]', 'global', $3::uuid) RETURNING id, procedure_id", name, goal_text, goal_id)
    return str(row["id"]), str(row["procedure_id"])


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{TAG}%")
    await p.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{TAG}%")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{TAG}%")
    await p.execute("DELETE FROM goal_search_index WHERE canonical_name LIKE $1", f"{TAG}%")
    await p.execute(
        "DELETE FROM object_routes WHERE object_id NOT IN (SELECT id FROM goals "
        "UNION SELECT procedure_id FROM procedures UNION SELECT id FROM knowledge_nodes)")
    await p.close()


@pytest.mark.asyncio
async def test_canonical_write_records_route_and_outbox_atomically(pool):
    gid = await _mk_goal(pool, f"{TAG} route goal")
    assert await pool.fetchval(
        "SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", gid) == HOME_SHARD
    assert await pool.fetchval(
        "SELECT count(*) FROM projection_outbox WHERE status='pending' AND object_id=$1::uuid", gid) == 1
    await pool.execute("UPDATE goals SET description='x' WHERE id=$1::uuid", gid)
    await pool.execute("UPDATE goals SET description='y' WHERE id=$1::uuid", gid)
    assert await pool.fetchval(
        "SELECT count(*) FROM projection_outbox WHERE status='pending' AND object_id=$1::uuid", gid) == 1


@pytest.mark.asyncio
async def test_drain_projects_goal_procedure_claim_and_replay_is_idempotent(pool):
    gid = await _mk_goal(pool, f"{TAG} find callers", desc="locate callers")
    _rid, pid = await _mk_proc(pool, gid, f"{TAG} proc", "find callers")
    cid = str(await pool.fetchval(
        "INSERT INTO knowledge_nodes (node_type, name, scope_type, claim_status) "
        "VALUES ('claim', $1, 'global', 'supported') RETURNING id", f"{TAG} claim statement"))
    await sp.drain_outbox(pool)
    g = await pool.fetchrow("SELECT * FROM goal_search_index WHERE goal_id=$1::uuid", gid)
    p = await pool.fetchrow("SELECT * FROM procedure_search_index WHERE procedure_id=$1::uuid", pid)
    assert g["home_shard_id"] == HOME_SHARD and "callers" in g["search_text"]
    assert str(p["goal_id"]) == gid
    assert await pool.fetchval("SELECT count(*) FROM claim_search_index WHERE claim_id=$1::uuid", cid) == 1
    await sp.reindex(pool, "goal")
    await sp.reindex(pool, "goal")
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE goal_id=$1::uuid", gid) == 1


@pytest.mark.asyncio
async def test_lost_projection_is_detected_and_repaired(pool):
    await sp.drain_outbox(pool)
    gid = await _mk_goal(pool, f"{TAG} repair goal")
    rep = await sp.verify_projection(pool)
    assert rep["types"]["goal"]["lagging"] >= 1 and rep["types"]["goal"]["missing"] == 0
    await sp.drain_outbox(pool)
    await pool.execute("DELETE FROM goal_search_index WHERE goal_id=$1::uuid", gid)
    bad = await sp.verify_projection(pool)
    assert not bad["ok"] and bad["types"]["goal"]["missing"] >= 1
    await sp.reindex(pool, "goal")
    ok = await sp.verify_projection(pool)
    assert ok["types"]["goal"]["missing"] == 0
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE goal_id=$1::uuid", gid) == 1


@pytest.mark.asyncio
async def test_merged_goal_is_removed_from_projection(pool):
    a = await _mk_goal(pool, f"{TAG} merge a")
    b = await _mk_goal(pool, f"{TAG} merge b")
    await sp.drain_outbox(pool)
    await pool.execute("UPDATE goals SET status='merged', merged_into_id=$2::uuid WHERE id=$1::uuid", a, b)
    await sp.drain_outbox(pool)
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE goal_id=$1::uuid", a) == 0


@pytest.mark.asyncio
async def test_remote_shard_canonical_is_projected_via_its_own_pool(pool):
    """A second pool with its own schema (search_path) plays a remote shard:
    the canonical row exists ONLY there; the projection is built from it."""
    await pool.execute("DROP SCHEMA IF EXISTS k900 CASCADE")
    await pool.execute("CREATE SCHEMA k900")
    await pool.execute("CREATE TABLE k900.goals (LIKE public.goals INCLUDING DEFAULTS)")
    dsn = os.environ["DATABASE_URL"]
    os.environ["K900_DSN"] = dsn + ("&" if "?" in dsn else "?") + "options=-csearch_path%3Dk900"
    await register_shard(pool, "K900", dsn_env="K900_DSN")
    pools = ShardPools(pool)
    gid = str(uuid.uuid4())
    shard_pool = await pools.get("K900")
    await shard_pool.execute(
        "INSERT INTO goals (id, canonical_name, normalized_name, status, scope_type, home_shard_id) "
        "VALUES ($1::uuid, $2, 'remote', 'active', 'global', 'K900')", gid, f"{TAG} remote goal")
    await pool.execute(
        "INSERT INTO object_routes (object_type, object_id, home_shard_id) VALUES ('goal', $1::uuid, 'K900')", gid)
    await sp.enqueue(pool, "goal", gid)
    await sp.drain_outbox(pool, pools=pools)
    row = await pool.fetchrow("SELECT home_shard_id FROM goal_search_index WHERE goal_id=$1::uuid", gid)
    assert row["home_shard_id"] == "K900"

    # without pools the remote object cannot be read: retried, never silently dropped
    await pool.execute("DELETE FROM goal_search_index WHERE goal_id=$1::uuid", gid)
    await sp.enqueue(pool, "goal", gid)
    r = await sp.drain_outbox(pool, pools=None)
    assert r["retry"] == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM projection_outbox WHERE status='pending' AND object_id=$1::uuid", gid) == 1
    await sp.drain_outbox(pool, pools=pools)
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE goal_id=$1::uuid", gid) == 1

    await pools.close()
    await pool.execute("DELETE FROM goal_search_index WHERE goal_id=$1::uuid", gid)
    await pool.execute("DELETE FROM object_routes WHERE object_id=$1::uuid", gid)
    await pool.execute("DELETE FROM projection_outbox WHERE object_id=$1::uuid", gid)
    await pool.execute("DROP SCHEMA k900 CASCADE")
    await pool.execute("DELETE FROM knowledge_shards WHERE shard_id='K900'")
