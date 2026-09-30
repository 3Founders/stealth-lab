"""Storage layout v2 (migration 132, docs/storage_layout_v2.md): spreading the search/log database B over a group of
search members is ONLY a placement change. This test proves it by difference: one corpus, every read path that the
group touches run twice -- first with no member (everything on one database), then after a member is registered and
the projections rebuilt (Goal text and vectors on the member, the control row slim) -- and the answers must be
IDENTICAL: retrieval (goal candidates, fused ranks, resolution, procedures), the hierarchy neighbourhood ranking,
product Goal search pages, the more-specific-Goal ordering, the routing prior's Goal vectors and projection checks.

Requires DATABASE_URL (with no search member registered) and SEARCH_GROUP_TEST_DSN (a separately migrated database
that plays the member)."""
import os

import pytest

from app.services import product_model as pm
from app.services import search_group
from app.services import search_projection as sp
from app.services.retrieval_service import QueryContext, _fetch_hierarchy_edges
from app.services.shards import invalidate_shard_cache, register_shard
from app.services.access import TenantScope
from tests.identity_fakes import make_judge
from tests.test_retrieval_golden_e2e import (  # noqa: F401 -- `pool` is the golden fixture (cleanup included)
    EMB, G_CALLERS, G_DELETE, G_DEPLOY, G_MIG_MY, G_MIG_PG, P, SCOPE, add_proc, build, jev, n, pool, query,
)

MEMBER_DSN = os.environ.get("SEARCH_GROUP_TEST_DSN")
pytestmark = pytest.mark.skipif(not (os.environ.get("DATABASE_URL") and MEMBER_DSN),
                                reason="needs DATABASE_URL + SEARCH_GROUP_TEST_DSN")
MEMBER = "S901"
G_MIG = "migrate a database"                 # the abstract Goal both migration Goals specialize
EXTRA = [f"database task number {i}" for i in range(7)]   # enough same-word Goals for several product-search pages
QUERIES = ["find callers of a function", "discover usages", "migrate the database schema", "deploy the service",
           "database", "schema on postgres"]


async def _corpus(pool) -> dict[str, str]:
    from app.services.goals import find_or_create_goal
    from tests.test_retrieval_golden_e2e import ingest_judge

    await build(pool, with_ways=True)
    for g in [G_MIG, *EXTRA]:
        await find_or_create_goal(pool, canonical_name=n(g), scope_type="global", provenance="prior_library",
                                  embedder=EMB, judge=ingest_judge(), status="active")
    await add_proc(pool, f"way to {G_MIG}", G_MIG)
    ids = {r["canonical_name"]: str(r["id"]) for r in await pool.fetch(
        "SELECT id, canonical_name FROM goals WHERE canonical_name LIKE $1", f"{P} %")}
    for specific in (G_MIG_PG, G_MIG_MY):
        await pool.execute(
            "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, scope_type, decided_by, decision_metadata) "
            "VALUES ($1::uuid, $2::uuid, 'SPECIALIZES', 'accepted', 'global', 'test', '{\"by\": \"test\"}')", ids[n(specific)], ids[n(G_MIG)])
    await pool.execute("UPDATE goals SET resolved_at = now() WHERE id = $1::uuid", ids[n(EXTRA[0])])
    await pool.execute("INSERT INTO projection_outbox (object_type, object_id) SELECT 'goal', id FROM goals "
                       "WHERE canonical_name LIKE $1 ON CONFLICT DO NOTHING", f"{P} %")
    await sp.drain_outbox(pool)
    return ids


def _retrieval_view(res: dict) -> dict:
    gr = res["goal_resolution"]
    return {
        "status": gr["status"],
        "goals": [(g["name"], g.get("id")) for g in gr["goals"]],
        "candidates": [(c["name"], c.get("fts_rank"), c.get("vec_rank"), c.get("rrf"), c.get("score"))
                       for c in gr["candidates"]],
        "procedures": [(p.get("id"), p.get("name")) for p in res.get("procedures") or []],
        "mode": res["retrieval"]["mode"],
        "routing": res["retrieval"].get("goal_routing"),
    }


async def _observe(pool, ids: dict[str, str]) -> dict:
    from app.execution.goal_resolution import _specific_goal_ids
    from app.routing import store as routing_store

    out: dict = {}
    for q in QUERIES:
        out[f"retrieval:{q}"] = _retrieval_view(await query(pool, q, judge=make_judge(jev())))
    seeds = [ids[n(G_MIG)], ids[n(G_MIG_PG)]]
    for q in QUERIES:
        emb = await EMB.embed_one(q)
        ctx = QueryContext(query=q, claims=[], text=q, query_embedding=emb, embedding_model=EMB.embedding_model_id())
        for fanout in (1, 2, 5):
            rows = await _fetch_hierarchy_edges(pool, up_ids=seeds, down_ids=seeds, scope=SCOPE,
                                                tenant_scope=TenantScope.unrestricted(), fanout=fanout, ctx=ctx)
            out[f"hierarchy:{q}:{fanout}"] = [
                (r["node_id"], r["side"], r["neighbour_id"], r["side_rank"], r["side_total"],
                 round(float(r["neighbour_semantic"]), 9), float(r["neighbour_lexical"])) for r in rows]
        out[f"specific:{q}"] = await _specific_goal_ids(pool, ids[n(G_MIG)], q, access_scope=SCOPE)
    for q in ("database", "callers", "migrate schema"):
        for resolved in (None, True, False):
            for offset in (0, 2, 4):
                page, more = await pm.find_goal(pool, q, scope=SCOPE, resolved=resolved, limit=2, offset=offset)
                out[f"find_goal:{q}:{resolved}:{offset}"] = ([g["id"] for g in page], more)
    rows = await routing_store.goal_rows(pool, list(ids.values()))
    out["routing_goal_rows"] = {k: (v["visibility"], v["embedding"]) for k, v in sorted(rows.items())}
    out["routing_visible_goal"] = (await routing_store.visible_goal(pool, ids[n(G_CALLERS)], SCOPE))["embedding"]
    # this corpus's projections, by id (a whole-database check would compare other tests' leftovers)
    doc_table = "goal_search_docs" if await search_group.grouped(pool) else "goal_search_index"
    out["projected_goals"] = sorted(r["id"] for r in await search_group.fetch_all(
        pool, f"SELECT goal_id::text AS id FROM {doc_table} WHERE goal_id = ANY($1::uuid[]) AND search_text IS NOT NULL",
        list(ids.values())))
    return out


@pytest.mark.asyncio
async def test_a_search_group_answers_exactly_what_one_database_answers(pool):
    if await search_group.grouped(pool):
        pytest.skip("a search member is already registered on this control database")
    ids = await _corpus(pool)
    single = await _observe(pool, ids)
    # the comparison is only as strong as what it compares: every path must have produced real, discriminating data
    hier = single["hierarchy:migrate the database schema:5"]
    assert hier and any(h[5] > 0 for h in hier) and any(h[6] > 0 for h in hier)       # semantic AND lexical scores
    assert len(single["hierarchy:migrate the database schema:1"]) < len(hier)          # the fan-out cut bites
    assert single["specific:migrate the database schema"]
    # text rank, not recency, orders the more-specific Goals: the older postgres Goal first for a postgres query
    assert single["specific:schema on postgres"] != single["specific:database"]
    assert single["find_goal:database:None:0"][0] and single["find_goal:database:None:0"][1]   # a page with a next
    assert single["find_goal:database:True:0"][0] != single["find_goal:database:False:0"][0]
    assert all(emb is not None for _vis, emb in single["routing_goal_rows"].values())
    assert single["retrieval:find callers of a function"]["goals"]
    assert any(c[2] is not None for c in single["retrieval:discover usages"]["candidates"])   # vector leg found it

    os.environ["S901_DSN"] = MEMBER_DSN
    await register_shard(pool, MEMBER, dsn_env="S901_DSN", role="search")
    invalidate_shard_cache()
    try:
        assert await search_group.grouped(pool)
        await sp.reindex(pool)
        # the control row is slim now: the Goal's text and vector are on the member
        slim = await pool.fetch("SELECT search_text, embedding FROM goal_search_index WHERE canonical_name LIKE $1",
                                f"{P} %")
        assert slim and all(r["search_text"] is None and r["embedding"] is None for r in slim)
        assert await search_group.fetchval_sum(
            pool, "SELECT count(*) FROM goal_search_docs WHERE canonical_name LIKE $1", f"{P} %") == len(slim)
        grouped = await _observe(pool, ids)

        for key in single:
            assert grouped[key] == single[key], key

        # a member that cannot be reached: identity/dedup reads fail (as one database that is down does -- never a
        # decision on a partial view), read-only retrieval still answers from the members that can
        from app.services.claim_identity import _candidates as claim_candidates
        from app.services.shards import ShardUnavailable

        await register_shard(pool, "S902", dsn_env="S902_DSN_NOT_SET", role="search")
        invalidate_shard_cache()
        try:
            with pytest.raises(ShardUnavailable):
                await search_group.fetchrow_any(pool, "SELECT 1 FROM identity_decisions LIMIT 1")
            with pytest.raises(ShardUnavailable):
                await claim_candidates(pool, "a claim", scope_type="global", scope_entity_id=None,
                                       visibility="public", owner_id=None, embedding=None, embedding_model=None)
            res = await query(pool, G_CALLERS, judge=make_judge(jev()))
            assert _retrieval_view(res)["goals"] == single[f"retrieval:{G_CALLERS}"]["goals"]
        finally:
            await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = 'S902'")
            invalidate_shard_cache()

        # a lost member doc is detected like a lost projection row on one database, and repaired by reindex
        before = (await sp.verify_projection(pool))["types"]["goal"]["docs_missing"]
        await search_group.execute_all(pool, "DELETE FROM goal_search_docs WHERE goal_id = $1::uuid", ids[n(G_DEPLOY)])
        bad = await sp.verify_projection(pool)
        assert not bad["ok"] and bad["types"]["goal"]["docs_missing"] == before + 1
        await sp.reindex(pool, "goal")
        assert (await sp.verify_projection(pool))["types"]["goal"]["docs_missing"] == 0
    finally:
        await search_group.execute_all(pool, "DELETE FROM goal_search_docs WHERE canonical_name LIKE $1", f"{P} %")
        await search_group.execute_all(pool, "DELETE FROM procedure_search_index WHERE name LIKE $1", f"{P} %")
        await search_group.execute_all(pool, "DELETE FROM claim_search_index")
        await search_group.execute_all(pool, "DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{P} %")
        await search_group.execute_all(pool, "DELETE FROM retrieval_decisions WHERE created_at > now() - interval '1 hour'")
        await pool.execute("DELETE FROM search_routes")
        await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = $1", MEMBER)
        invalidate_shard_cache()
        await sp.reindex(pool)          # back to one database: full control rows again
