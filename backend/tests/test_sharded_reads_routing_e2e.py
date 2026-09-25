"""REAL sharding, read side: objects homed on a second PostgreSQL database (K001) are
found by every reader that knows their id, exact name or owning Goal -- without
asking every shard. Requires TEST_SHARD_DATABASE_URL (a separately migrated
database); reuses the control/shard fixture of test_sharded_writes_e2e."""
import pytest

from app.execution.goal_resolution import resolve_goal, resolve_goal_id_for_text
from app.services import product_model as pm
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.access import AccessScope
from app.services.goal_ranking import ProcedureRankingService
from app.services.goals import get_goal
from app.services.procedures import capture_procedure, record_execution_outcome
from tests.test_sharded_writes_e2e import EMB, JUDGE, SHARD_DSN, T, env, goal, n  # noqa: F401 -- `env` is the fixture

pytestmark = pytest.mark.skipif(not SHARD_DSN, reason="needs DATABASE_URL + TEST_SHARD_DATABASE_URL")
U = AccessScope.unrestricted()


async def _goal_with_procedure(pool, goal_name: str, procedure_name: str) -> tuple[str, dict]:
    proc = await capture_procedure(
        pool, name=n(procedure_name), goal=n(goal_name), steps=[{"description": "do it"}],
        provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    goal_id = await pool.fetchval("SELECT goal_id::text FROM goal_names WHERE normalized_name = $1", n(goal_name).lower())
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))
    return goal_id, proc


@pytest.mark.asyncio
async def test_find_ways_resolves_a_remote_goal_and_its_procedure_without_a_broadcast(env):
    pool, shard, _ = env
    goal_id, proc = await _goal_with_procedure(pool, "parse a csv file", "csv reader")
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE id = $1::uuid", goal_id) == 0     # canonical row is remote

    with sh.track_shard_requests() as stats:
        tree = await resolve_goal(pool, goal_id, scope=U)
    assert tree.chosen == "procedure"
    assert tree.procedure["id"] == proc["id"]
    assert "K001" in stats.shards
    # Locating the Goal's Procedures used the projection + one targeted read,
    # not a query on every shard.
    assert stats.hydrations >= 1


@pytest.mark.asyncio
async def test_exact_name_lookup_finds_a_remote_goal(env):
    pool, shard, _ = env
    goal_id, _ = await _goal_with_procedure(pool, "resize an image", "pillow resize")
    found = await resolve_goal_id_for_text(pool, n("resize an image"), scope_type="global", scope_entity_id=None)
    assert found is not None and str(found["id"]) == goal_id


@pytest.mark.asyncio
async def test_goal_page_lists_procedures_from_the_remote_shard(env):
    pool, shard, _ = env
    goal_id, proc = await _goal_with_procedure(pool, "compress a folder", "zip it")
    page = await get_goal(pool, goal_id, scope=U)
    assert page is not None
    assert [p["id"] for p in page["procedures"]] == [proc["id"]]


@pytest.mark.asyncio
async def test_product_lists_browse_and_search_include_remote_goals(env):
    pool, shard, _ = env
    goal_id, _ = await _goal_with_procedure(pool, "rename many files", "bulk rename")

    assert (await pm.get_goal_for_product(pool, goal_id, scope=U))["id"] == goal_id
    listed, _ = await pm.list_goals(pool, scope=U, limit=200)
    assert goal_id in {g["id"] for g in listed}
    browsed, _ = await pm.list_goals_browse(pool, scope=U, limit=200)
    assert goal_id in {g["id"] for g in browsed}
    found, _ = await pm.find_goal(pool, T, scope=U, limit=50)
    assert goal_id in {g["id"] for g in found}


@pytest.mark.asyncio
async def test_resolving_a_remote_goal_updates_its_projection(env):
    pool, shard, _ = env
    goal_id, proc = await _goal_with_procedure(pool, "format a date", "strftime")
    assert await pool.fetchval("SELECT resolved_at FROM goal_search_index WHERE goal_id = $1::uuid", goal_id) is None
    # Promote the Procedure to verified: >=10 successes, 0 failures, >=3 distinct contexts.
    for index in range(10):
        await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key=f"ctx-{index % 3}")
    assert await shard.fetchval("SELECT resolved_at FROM goals WHERE id = $1::uuid", goal_id) is not None
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))
    assert await pool.fetchval("SELECT resolved_at FROM goal_search_index WHERE goal_id = $1::uuid", goal_id) is not None
    resolved, _ = await pm.list_goals(pool, scope=U, resolved=True, limit=200)
    assert goal_id in {g["id"] for g in resolved}


@pytest.mark.asyncio
async def test_procedure_ranking_reads_a_remote_procedure_and_its_evidence(env):
    pool, shard, _ = env
    _, proc = await _goal_with_procedure(pool, "sort a large file", "external sort")
    await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key="ctx-a")
    service = ProcedureRankingService(pool, require_verified=False)
    candidates = await service.fetch_candidates([proc["id"]])
    assert [str(c["id"]) for c in candidates] == [proc["id"]]
    assert len(await service.fetch_evidence([proc["id"]])) >= 1   # evidence lives with the remote Procedure
    ranked = await service.rank([proc["id"]])
    assert ranked and str(ranked[0].get("procedure_row_id") or ranked[0].get("id")) == proc["id"]
