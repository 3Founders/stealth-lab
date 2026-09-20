"""REAL sharding: canonical writes to a second PostgreSQL database (K001), routed by the
control database. Requires TEST_SHARD_DATABASE_URL (a separately migrated database)."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.access import AccessScope
from app.services.goals import find_or_create_goal
from app.services.procedures import capture_procedure, get_procedure, record_execution_outcome, supersede_procedure
from app.services.retrieval_service import find_best_way
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

SHARD_DSN = os.environ.get("TEST_SHARD_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (os.environ.get("DATABASE_URL") and SHARD_DSN), reason="needs DATABASE_URL + TEST_SHARD_DATABASE_URL")
T = "shd" + uuid.uuid4().hex[:5]
EMB = ConceptEmbedder()
KW = dict(scope_type="global", provenance="prior_library", created_from="test")


def n(x):
    return f"{T} {x}"


def fn(kind, a, b):
    al, bl = a.lower(), b.lower()
    if T not in bl:
        return {"goal": ("distinct", .9), "procedure": ("distinct", .9), "task_goal": ("unrelated", .9), "task_procedure": ("not_applicable", .9)}[kind]
    if kind == "goal":
        return ("same", .95) if ("call site" in al) != ("call site" in bl) and "callers" in al + bl else ("distinct", .9)
    if kind == "task_goal":
        return ("matches", .9)
    if kind == "task_procedure":
        return ("applies", .9)
    return ("distinct", .9)


JUDGE = make_judge(CallbackProvider(fn, name="jev"))


async def _wipe(pool, shard):
    """Test hygiene: remove everything earlier runs left on the shard and the routes that pointed at it."""
    await shard.execute("DELETE FROM procedures WHERE name LIKE 'shd%'")
    await shard.execute("DELETE FROM goals WHERE canonical_name LIKE 'shd%'")
    await pool.execute("DELETE FROM object_routes WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM procedure_row_routes WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM goal_names WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM goal_search_index WHERE home_shard_id = 'K001'")
    await pool.execute("DELETE FROM procedure_search_index WHERE home_shard_id = 'K001'")


@pytest_asyncio.fixture
async def env():
    pool = await create_pool()
    shard = await create_pool(SHARD_DSN)
    os.environ["K001_DATABASE_URL"] = SHARD_DSN
    await _wipe(pool, shard)
    await sh.register_shard(pool, "K001", dsn_env="K001_DATABASE_URL", weight=100)
    await sh.register_shard(pool, "K000", dsn_env=None, weight=0)          # public placement -> K001 only
    pools = sh.pools_for(pool)
    yield pool, shard, pools
    for p in (pool, shard):
        await p.execute("DELETE FROM procedures WHERE name LIKE $1 AND NOT EXISTS (SELECT 1 FROM execution_plans e WHERE e.procedure_row_id = procedures.id)", f"{T}%")
        await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1 AND NOT EXISTS (SELECT 1 FROM procedures x WHERE x.achieves_goal_id = goals.id)", f"{T}%")
    await pool.execute("DELETE FROM goal_search_index WHERE canonical_name LIKE $1", f"{T}%")
    await pool.execute("DELETE FROM procedure_search_index WHERE name LIKE $1", f"{T}%")
    await pool.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{T}%")
    await pool.execute("DELETE FROM goal_names WHERE normalized_name LIKE $1", f"{T}%")
    await _wipe(pool, shard)
    await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
    await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = 'K001'")     # leave a single-shard deployment behind
    sh.invalidate_shard_cache()
    await shard.close()
    await pool.close()


async def goal(pool, name, **kw):
    return await find_or_create_goal(pool, canonical_name=n(name), embedder=EMB, judge=JUDGE, **{**KW, **kw})


@pytest.mark.asyncio
async def test_public_goal_is_written_to_the_remote_database_only(env):
    pool, shard, _ = env
    g = await goal(pool, "find callers of a function")
    assert g["created"] and g["home_shard_id"] == "K001"
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE id=$1::uuid", g["id"]) == 1          # canonical row on the shard
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE id=$1::uuid", g["id"]) == 0           # NOT in the control DB
    assert await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", g["id"]) == "K001"
    assert await pool.fetchval("SELECT goal_id::text FROM goal_names WHERE normalized_name=$1", n("find callers of a function").lower()) == g["id"]
    proj = await pool.fetchrow("SELECT home_shard_id, canonical_name FROM goal_search_index WHERE goal_id=$1::uuid", g["id"])
    assert proj["home_shard_id"] == "K001"                                                              # searchable immediately


@pytest.mark.asyncio
async def test_exact_duplicates_across_concurrent_workers_create_one_remote_goal(env):
    pool, shard, _ = env
    res = await asyncio.gather(*[goal(pool, "rotate the api keys") for _ in range(8)])
    assert len({r["id"] for r in res}) == 1 and sum(r["created"] for r in res) == 1
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE canonical_name=$1", n("rotate the api keys")) == 1


@pytest.mark.asyncio
async def test_paraphrase_of_a_remote_goal_is_judged_and_reuses_it(env):
    pool, shard, _ = env
    a = await goal(pool, "find callers of a function")
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))
    b = await goal(pool, "locate every call site of a function")
    assert not b["created"] and b["id"] == a["id"] and b["decision"] == "same"
    assert n("locate every call site of a function") in await shard.fetchval("SELECT aliases FROM goals WHERE id=$1::uuid", a["id"])


@pytest.mark.asyncio
async def test_procedure_is_colocated_with_its_remote_goal_and_execution_can_reference_it(env):
    pool, shard, _ = env
    r = await capture_procedure(pool, name=n("grep callers"), goal=n("find callers of a function"), steps=[{"description": "rg"}],
                                provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    assert await shard.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id=$1::uuid", r["id"])
    assert await pool.fetchval("SELECT count(*) FROM procedures WHERE id=$1::uuid", r["id"]) == 0
    assert await pool.fetchval("SELECT home_shard_id FROM procedure_row_routes WHERE row_id=$1::uuid", r["id"]) == "K001"
    row = await get_procedure(pool, r["id"])                                                            # transparently read from the shard
    assert row["name"] == n("grep callers")
    compiled = compile_plan(procedure_id=uuid.UUID(r["procedure_id"]), procedure_version=1, procedure_row_id=uuid.UUID(r["id"]),
                            procedure_payload={"steps": row["steps"]}, task_description=f"{T} task", scope_type="global",
                            extractor_version="t@1", nodes=[{"order": 0, "goal": "go"}])
    persisted, new = await persist_compiled_plan(pool, compiled)                                        # execution plan (control DB) -> remote procedure
    assert new
    bogus = compile_plan(procedure_id=uuid.uuid4(), procedure_version=1, procedure_row_id=uuid.uuid4(), procedure_payload={"steps": []},
                         task_description=f"{T} bogus", scope_type="global", extractor_version="t@1", nodes=[{"order": 0, "goal": "go"}])
    with pytest.raises(Exception, match="neither local nor routed"):                                    # the FK replacement still guards references
        await persist_compiled_plan(pool, bogus)


@pytest.mark.asyncio
async def test_versions_and_execution_evidence_live_with_the_remote_procedure(env):
    pool, shard, _ = env
    r = await capture_procedure(pool, name=n("lsp refs"), goal=n("find callers of a function"), steps=[{"description": "a"}],
                                provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    v2 = await supersede_procedure(pool, prior_row_id=r["id"], changed_fields={"steps": [{"description": "b"}]})
    assert v2["version"] == 2
    assert await shard.fetchval("SELECT achieves_goal_id IS NOT NULL FROM procedures WHERE id=$1::uuid", v2["id"])
    assert await pool.fetchval("SELECT home_shard_id FROM procedure_row_routes WHERE row_id=$1::uuid", v2["id"]) == "K001"
    out = await record_execution_outcome(pool, procedure_row_id=v2["id"], success=True, context_key="c1")
    assert out["verification_stats"]["attempts"] == 1
    assert await shard.fetchval("SELECT count(*) FROM evidence WHERE target_id=$1::uuid", v2["id"]) == 1     # evidence colocated
    assert await pool.fetchval("SELECT count(*) FROM evidence WHERE target_id=$1::uuid", v2["id"]) == 0


@pytest.mark.asyncio
async def test_retrieval_hydrates_from_the_remote_shard_and_only_touches_it(env):
    pool, shard, pools = env
    await capture_procedure(pool, name=n("grep callers"), goal=n("find callers of a function"), steps=[{"description": "rg"}],
                            provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    await sp.drain_outbox(pool, pools=pools)
    res = await find_best_way(pool, f"{T} find callers of a function", scope=AccessScope.unrestricted(), embedder=EMB, judge=JUDGE,
                              pools=pools, require_verified=False)
    assert [p["name"] for p in res["procedures"]] == [n("grep callers")]
    assert res["retrieval"]["shards_touched"] == ["K001"] and not res["retrieval"]["degraded"]


@pytest.mark.asyncio
async def test_private_knowledge_never_leaves_the_home_shard(env):
    pool, shard, _ = env
    g = await goal(pool, "my private plan", visibility="private", owner_id="alice")
    assert g["home_shard_id"] == "K000"
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE id=$1::uuid", g["id"]) == 1
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE id=$1::uuid", g["id"]) == 0


@pytest.mark.asyncio
async def test_rollover_new_objects_move_existing_stay(env):
    pool, shard, _ = env
    a = await goal(pool, "goal before rollover")
    await sh.set_shard_status(pool, "K001", "full")
    await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
    b = await goal(pool, "goal after rollover")
    assert (a["home_shard_id"], b["home_shard_id"]) == ("K001", "K000")
    p = await capture_procedure(pool, name=n("proc for old goal"), goal=n("goal before rollover"), steps=[{"description": "x"}],
                                provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    assert await pool.fetchval("SELECT count(*) FROM procedures WHERE id=$1::uuid", p["id"]) == 1     # rolled over to K000...
    link = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id=$1::uuid", p["id"])
    assert link == a["id"]                                                                              # ...but still linked to the K001 goal


@pytest.mark.asyncio
async def test_cross_shard_goal_merge_repoints_procedures_everywhere(env):
    pool, shard, _ = env
    a = await goal(pool, "keep this goal")                               # -> K001
    await sh.set_shard_status(pool, "K001", "readonly")
    await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
    b = await goal(pool, "duplicate of it")                              # -> K000
    p = await capture_procedure(pool, name=n("proc on dup"), goal=n("duplicate of it"), steps=[{"description": "x"}],
                                provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    from app.services.identity_resolution import merge_goal
    await merge_goal(pool, b["id"], a["id"])
    assert await pool.fetchval("SELECT status FROM goals WHERE id=$1::uuid", b["id"]) == "merged"
    assert await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id=$1::uuid", p["id"]) == a["id"]
    assert n("duplicate of it") in await shard.fetchval("SELECT aliases FROM goals WHERE id=$1::uuid", a["id"])
    assert await pool.fetchval("SELECT count(*) FROM goal_names WHERE goal_id=$1::uuid", b["id"]) == 0


@pytest.mark.asyncio
async def test_verify_refs_detects_a_dangling_route(env):
    pool, shard, _ = env
    g = await goal(pool, "verifiable goal")
    rep = await sh.verify_routes(pool)
    assert rep["ok"], rep
    await shard.execute("DELETE FROM goals WHERE id=$1::uuid", g["id"])                                # shard lost the row
    bad = await sh.verify_routes(pool)
    assert not bad["ok"] and bad["shards"]["K001"]["goal"]["dangling"] >= 1
    await pool.execute("DELETE FROM object_routes WHERE object_id=$1::uuid", g["id"])
    await pool.execute("DELETE FROM goal_names WHERE goal_id=$1::uuid", g["id"])
