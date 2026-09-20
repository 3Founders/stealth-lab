"""Every reader/writer of `procedures` works when the row lives ONLY on a remote shard (K001): by-id readers go to the
home shard, scans fan out. Requires DATABASE_URL (control) + TEST_SHARD_DATABASE_URL (a separately migrated database)."""
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import shards as sh
from app.services.access import AccessScope
from app.services.goals import find_or_create_goal
from app.services.procedures import capture_procedure
from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge

SHARD_DSN = os.environ.get("TEST_SHARD_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (os.environ.get("DATABASE_URL") and SHARD_DSN), reason="needs DATABASE_URL + TEST_SHARD_DATABASE_URL")
T = "shr" + uuid.uuid4().hex[:5]
EMB, JUDGE = ConceptEmbedder(), make_judge(FrozenProvider({}))


@pytest_asyncio.fixture
async def env():
    pool = await create_pool()
    shard = await create_pool(SHARD_DSN)
    os.environ["K001_DATABASE_URL"] = SHARD_DSN
    await sh.register_shard(pool, "K001", dsn_env="K001_DATABASE_URL", weight=100)
    await sh.register_shard(pool, "K000", dsn_env=None, weight=0)       # public placement -> K001 only
    sh.invalidate_shard_cache()
    r = await capture_procedure(
        pool, name=f"{T} proc", goal=f"{T} reader goal", provenance="prior_library", scope_type="global", created_by=f"{T}-user",
        owner_id=f"{T}-user", steps=[{"order": 0, "description": "x", "source_locator": {"uri": "u"}}], goal_embedder=EMB, goal_judge=JUDGE,
        source_locator={"uri": "u"})
    yield pool, shard, r
    for p in (pool, shard):
        await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{T}%")
        await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T}%")
    for tbl in ("goal_search_index", "procedure_search_index"):
        await pool.execute(f"DELETE FROM {tbl} WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM object_routes WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM procedure_row_routes WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM goal_names WHERE normalized_name LIKE $1", f"{T}%")
    await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
    await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = 'K001'")
    sh.invalidate_shard_cache()
    await shard.close()
    await pool.close()


@pytest.mark.asyncio
async def test_the_procedure_really_lives_only_on_the_remote_shard(env):
    pool, shard, r = env
    assert await pool.fetchval("SELECT count(*) FROM procedures WHERE id=$1::uuid", r["id"]) == 0
    assert await shard.fetchval("SELECT count(*) FROM procedures WHERE id=$1::uuid", r["id"]) == 1


@pytest.mark.asyncio
async def test_by_id_readers_find_it(env):
    pool, _, r = env
    from app.execution.procedure_graph import fetch_procedure_version
    from app.mcp_server import server as srv
    from app.services.procedure_graph_api import get_procedure_detail, get_procedure_versions
    from app.stealth.local_sync import _fetch_procedure
    scope = AccessScope.unrestricted()
    assert (await get_procedure_detail(pool, str(r["id"]), scope=scope))["name"] == f"{T} proc"
    assert [v["name"] for v in await get_procedure_versions(pool, str(r["procedure_id"]), scope=scope)] == [f"{T} proc"]
    assert str((await fetch_procedure_version(pool, r["procedure_id"], 1))["id"]) == str(r["id"])
    assert str((await _fetch_procedure(pool, str(r["procedure_id"])))["id"]) == str(r["id"])
    assert str((await srv._resolve_live_procedure(pool, str(r["procedure_id"])))["id"]) == str(r["id"])


@pytest.mark.asyncio
async def test_scans_and_counts_fan_out(env):
    pool, _, r = env
    from app.execution.goal_resolution import _feasible_procedures_for_goal
    from app.services.contributors import contribution_counts
    from app.services.index_freshness import get_index_lag
    gid = await pool.fetchval("SELECT object_id FROM object_routes WHERE object_type='goal' AND home_shard_id='K001' LIMIT 1") or None
    # the goal of this procedure (its row is on K001; the link is routed)
    shard_goal = await (await sh.home_pool(pool, "procedure", str(r["procedure_id"]))).fetchval(
        "SELECT achieves_goal_id FROM procedures WHERE id=$1::uuid", r["id"])
    feas = await _feasible_procedures_for_goal(pool, str(shard_goal), current_scope={}, access_scope=AccessScope.unrestricted())
    assert [p["name"] for p, _ in feas] == [f"{T} proc"]
    counts = await contribution_counts(pool, user_id=str(uuid.uuid4()), subject=f"{T}-user")
    assert counts["procedures_authored"] == 1
    lag = await get_index_lag(pool, limit=5000)
    assert lag["total_stale"] >= 0 and isinstance(lag["sample"], list)
    assert gid is None or gid


@pytest.mark.asyncio
async def test_fanout_helpers_cover_control_plus_shard_and_skip_a_dead_shard_unless_strict(env):
    pool, _, r = env
    rows = await sh.fanout_fetch(pool, "SELECT id FROM procedures WHERE name = $1", f"{T} proc")
    assert [str(x["id"]) for x in rows] == [str(r["id"])]
    assert await sh.fanout_fetchval_sum(pool, "SELECT count(*) FROM procedures WHERE name = $1", f"{T} proc") == 1
    os.environ["K001_DATABASE_URL"] = ""                       # the shard becomes unreachable
    sh.pools_for(pool)._pools.pop("K001", None)
    sh.pools_for(pool)._failed_until.pop("K001", None)
    assert [s for s, _ in await sh.all_pools(pool)] == ["K000"]                          # lenient readers skip it (logged)
    with pytest.raises(sh.ShardUnavailable):
        await sh.all_pools(pool, strict=True)                                            # writers/verifiers must see it
    os.environ["K001_DATABASE_URL"] = SHARD_DSN
