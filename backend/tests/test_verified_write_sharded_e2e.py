"""The verified-solutions write path in the production layout of storage v2 (docs/storage_layout_v2.md): public Goals
placed on a knowledge shard (K000 at weight 0), optionally with the search/log database as a group of members.

One task is written through `write_task` (the pipeline's own function; the model calls are fakes) and every row must
be where its readers look: the Goal, Procedure, evidence and claims on the Goal's shard, their routes and projections
on the control database, the Benchmark on the control database, the claim refs where retrieval reads them. Then
retrieval must find the Procedure with its benchmark support counted, and a retry must reuse everything (no second
naming call, no second Goal).

Requires DATABASE_URL + TEST_SHARD_DATABASE_URL; the grouped case also SEARCH_GROUP_TEST_DSN."""
import json
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import search_group
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.access import AccessScope
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

SHARD_DSN = os.environ.get("TEST_SHARD_DATABASE_URL")
MEMBER_DSN = os.environ.get("SEARCH_GROUP_TEST_DSN")
pytestmark = pytest.mark.skipif(not (os.environ.get("DATABASE_URL") and SHARD_DSN),
                                reason="needs DATABASE_URL + TEST_SHARD_DATABASE_URL")
T = "vsh" + uuid.uuid4().hex[:6]


class _Embedder(ConceptEmbedder):
    """ConceptEmbedder with the batch surface the embedding sweep uses."""
    dimension = 1024

    def _configured_provider(self):          # the sweep stamps the provider next to the model id
        return "concept-fake"

    async def embed(self, texts, input_type="document"):
        return [await self.embed_one(t, input_type=input_type) for t in texts]


EMB = _Embedder()
GOAL_NAME = f"{T} make relative path conversion return posix style paths"
EXTRACTED = {"name": f"{T} normalise separators in relative paths",
             "steps": [{"do": "Find where the relative path is built", "role": "plan", "check": ""},
                       {"do": "Replace OS separators with forward slashes", "role": "edit", "check": ""},
                       {"do": "Run the failing test", "role": "verify", "check": "pytest t.py::test_a"}],
             "preconditions": ["Paths are built with os.path"], "pitfalls": [f"{T} using replace on backslash only"],
             "facts": [f"{T} relpath returns backslashes on windows"]}


class FakeClient:
    """The two model calls of the pipeline: task-Goal naming and extraction."""

    def __init__(self):
        self.naming_calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        system = kw["messages"][0]["content"]
        if system.startswith("You name the GOAL"):
            self.naming_calls += 1
            reply = json.dumps({"goal": GOAL_NAME})
        else:
            reply = json.dumps(EXTRACTED)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)


def _distinct(kind, a, b):
    return {"goal": ("distinct", .9), "procedure": ("distinct", .9), "task_goal": ("matches", .9),
            "task_procedure": ("applies", .9)}.get(kind, ("distinct", .9))


@pytest_asyncio.fixture(params=["one_search_db", "search_group"])
async def env(request, monkeypatch):
    if request.param == "search_group" and not MEMBER_DSN:
        pytest.skip("needs SEARCH_GROUP_TEST_DSN")
    from app.ingest.common import embedding as ingest_embedding
    from app.services import claims, identity_resolution

    monkeypatch.setattr(ingest_embedding, "_EMBEDDER", EMB)
    monkeypatch.setattr(claims, "Embedder", lambda: EMB)
    monkeypatch.setattr(identity_resolution, "_DEFAULT_JUDGE", make_judge(CallbackProvider(_distinct, name="jev")))
    pool = await create_pool()
    shard = await create_pool(SHARD_DSN)
    os.environ["K001_DATABASE_URL"] = SHARD_DSN
    await sh.register_shard(pool, "K001", dsn_env="K001_DATABASE_URL", weight=100)
    await sh.register_shard(pool, "K000", dsn_env=None, weight=0)          # public placement -> K001 only
    await shard.execute("INSERT INTO sl_database_role (singleton, role) VALUES (true, 'knowledge_shard') "
                        "ON CONFLICT (singleton) DO NOTHING")
    if request.param == "search_group":
        os.environ["S901_DSN"] = MEMBER_DSN
        await sh.register_shard(pool, "S901", dsn_env="S901_DSN", role="search")
    sh.invalidate_shard_cache()
    try:
        yield pool, shard, request.param == "search_group"
    finally:
        if request.param == "search_group":
            for t, c in (("goal_search_docs", "canonical_name"), ("procedure_search_index", "name"),
                         ("claim_search_index", "statement"), ("identity_decisions", "candidate_text")):
                await search_group.execute_all(pool, f"DELETE FROM {t} WHERE {c} LIKE $1", f"{T}%")
            await pool.execute("DELETE FROM search_routes")
            await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = 'S901'")
        await shard.execute("DELETE FROM sl_database_role")
        for p in (pool, shard):
            # evidence is append-only (invariant #19): its rows stay, keyed by this run's unique task
            await p.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{T}%")
            await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{T}%")
            await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T}%")
        await pool.execute("DELETE FROM procedure_claim_refs WHERE created_by = 'ingest:verified' AND NOT EXISTS "
                           "(SELECT 1 FROM procedures p WHERE p.procedure_id = procedure_claim_refs.procedure_id)")
        await pool.execute("DELETE FROM benchmarks WHERE metadata->>'task_key' LIKE $1", f"swe:{T}%")
        await pool.execute("DELETE FROM ingest_task_goals WHERE task_key LIKE $1", f"swe:{T}%")
        for t, c in (("goal_search_index", "canonical_name"), ("procedure_search_index", "name"),
                     ("claim_search_index", "statement"), ("identity_decisions", "candidate_text")):
            await pool.execute(f"DELETE FROM {t} WHERE {c} LIKE $1", f"{T}%")
        await pool.execute("DELETE FROM goal_names WHERE canonical_name LIKE $1", f"{T}%")
        for t in ("goal_search_index", "procedure_search_index", "claim_search_index", "goal_names"):
            await pool.execute(f"DELETE FROM {t} WHERE home_shard_id = 'K001'")      # leftovers of failed runs
        await pool.execute("DELETE FROM object_routes WHERE home_shard_id = 'K001'")
        await pool.execute("DELETE FROM procedure_row_routes WHERE home_shard_id = 'K001'")
        await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
        await pool.execute("DELETE FROM knowledge_shards WHERE shard_id = 'K001'")
        sh.invalidate_shard_cache()
        await shard.close()
        await pool.close()


def _task():
    from app.ingest.verified.sources import SOURCES, to_task

    row = {"instance_id": f"{T}__r-1", "repo": "o/r", "base_commit": "a" * 40,
           "problem_statement": "Relative paths come back with backslashes on Windows",
           "patch": "diff --git a/p.py b/p.py", "test_patch": "diff --git a/t.py b/t.py",
           "FAIL_TO_PASS": ["t.py::test_a"], "PASS_TO_PASS": ["t.py::test_b"], "FAIL_TO_FAIL": [], "PASS_TO_FAIL": [],
           "license_name": "MIT License", "docker_image": "img:1", "install_config": {"test_cmd": "pytest"},
           "meta": {"llm_score": {"test_score": 1}}, "hints_text": ""}
    return to_task(SOURCES["swe-rebench"], row), SOURCES["swe-rebench"]


@pytest.mark.asyncio
async def test_a_verified_task_is_written_where_its_readers_look(env):
    from app.ingest.verified.pipeline import write_task
    from app.services.retrieval_service import find_best_way

    pool, shard, grouped = env
    if not grouped and await search_group.grouped(pool):
        pytest.skip("this control database already has a search group: the one-database case cannot run here")
    assert await search_group.grouped(pool) == grouped
    task, src = _task()
    client = FakeClient()
    status, why, _detail, objects = await write_task(pool, task=task, src=src, spdx="MIT", how="row_github_name",
                                                     client=client, model="m", run_id=str(uuid.uuid4()))
    assert (status, why) == ("written", "written")
    gid, row_id, pid = objects["goal_id"], objects["procedure_row_id"], objects["procedure_id"]

    # canonical knowledge on the Goal's shard, never on the control database
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE id = $1::uuid", gid) == 1
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE id = $1::uuid", gid) == 0
    assert await shard.fetchval("SELECT count(*) FROM procedures WHERE id = $1::uuid", row_id) == 1
    assert await shard.fetchval("SELECT count(*) FROM evidence WHERE target_id = $1::uuid", row_id) == 1
    assert await pool.fetchval("SELECT count(*) FROM evidence WHERE target_id = $1::uuid", row_id) == 0
    assert len(objects["claim_ids"]) == 2
    for cid in objects["claim_ids"]:
        assert await shard.fetchval("SELECT count(*) FROM knowledge_nodes WHERE id = $1::uuid", cid) == 1
        assert await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE id = $1::uuid", cid) == 0
        assert await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type = 'claim' "
                                   "AND object_id = $1::uuid", cid) == "K001"
    # the shard keeps no control-plane rows of its own (migration 133's role marker)
    ids = [gid, pid, *objects["claim_ids"]]
    assert await shard.fetchval("SELECT count(*) FROM object_routes WHERE object_id = ANY($1::uuid[])", ids) == 0
    assert await shard.fetchval("SELECT count(*) FROM projection_outbox WHERE object_id = ANY($1::uuid[])", ids) == 0
    # control-database rows: benchmark, claim refs, task registration
    assert await pool.fetchval("SELECT goal_id::text FROM benchmarks WHERE id = $1::uuid", objects["benchmark_id"]) == gid
    assert await pool.fetchval("SELECT count(*) FROM procedure_claim_refs WHERE procedure_id = $1::uuid", pid) == 2
    # vectors on the shard rows (the embedding step found them there)
    assert await shard.fetchval("SELECT embedding IS NOT NULL FROM goals WHERE id = $1::uuid", gid)
    assert await shard.fetchval("SELECT embedding IS NOT NULL FROM procedures WHERE id = $1::uuid", row_id)

    # searchable, and retrieval counts the benchmark support read from the Procedure's shard
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))
    # this task's projections (a whole-database check would see other tests' leftovers in a shared database)
    doc_table = "goal_search_docs" if grouped else "goal_search_index"
    assert await search_group.fetchval_sum(pool, f"SELECT count(*) FROM {doc_table} WHERE goal_id = $1::uuid "
                                                 "AND search_text IS NOT NULL", gid) == 1
    assert await search_group.fetchval_sum(pool, "SELECT count(*) FROM procedure_search_index "
                                                 "WHERE procedure_id = $1::uuid AND home_shard_id = 'K001'", pid) == 1
    assert await search_group.fetchval_sum(pool, "SELECT count(*) FROM claim_search_index WHERE claim_id = ANY($1::uuid[]) "
                                                 "AND home_shard_id = 'K001'", objects["claim_ids"]) == 2
    res = await find_best_way(pool, "relative paths return posix style separators", local_claims=[],
                              scope=AccessScope.unrestricted(), embedder=EMB,
                              judge=make_judge(CallbackProvider(_distinct, name="jev")))
    procs = {p["procedure_id"]: p for p in res.get("procedures") or []}
    assert pid in procs, res["goal_resolution"]
    assert procs[pid]["source_support"] == 1 and procs[pid]["tested_by_source"]

    # a retry of the same task reuses the Goal without naming it again
    calls = client.naming_calls
    status2, _why2, _d2, objects2 = await write_task(pool, task=task, src=src, spdx="MIT", how="row_github_name",
                                                     client=client, model="m", run_id=str(uuid.uuid4()))
    assert status2 == "written" and objects2["goal_id"] == gid and client.naming_calls == calls
    assert await shard.fetchval("SELECT count(*) FROM goals WHERE canonical_name = $1", GOAL_NAME) == 1


@pytest.mark.asyncio
async def test_mcp_find_ways_serves_the_way_from_the_shard_layout(env, monkeypatch):
    """The v1 MCP tool an agent calls (`find_ways`) on storage layout v2: the Goal and its Procedure live on K001,
    the search text on the control database or a search member. find_ways must resolve the Goal and return the
    way with every step -- the same answer an agent got on one database. Timed, so a fan-out regression shows."""
    import time

    import app.mcp_server.server as srv
    from app.ingest.verified.pipeline import write_task

    pool, _shard, _grouped = env
    task, src = _task()
    status, _why, _detail, objects = await write_task(pool, task=task, src=src, spdx="MIT", how="row_github_name",
                                                      client=FakeClient(), model="m", run_id=str(uuid.uuid4()))
    assert status == "written"
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))

    monkeypatch.setattr("app.services.embeddings.Embedder", lambda *a, **k: EMB)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.anonymous())
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))
    t0 = time.perf_counter()
    out = json.loads(await srv.find_ways("relative paths return posix style separators", ctx, use_llm=False))
    elapsed = time.perf_counter() - t0
    blob = json.dumps(out)
    assert objects["procedure_id"] in blob or EXTRACTED["name"] in blob, (
        f"find_ways did not return the way written on K001: {blob[:1500]}")
    way = next(p for p in out["procedures"] if p["procedure_id"] == objects["procedure_id"])
    # the steps in full: ingested steps are {"do", "role", "check"}, and every one used to come back null
    assert [s["do"] for s in way["steps"]] == [s["do"] for s in EXTRACTED["steps"]]
    assert [s.get("role") for s in way["steps"]] == [s["role"] for s in EXTRACTED["steps"]]
    assert way["steps"][2]["check"] == "pytest t.py::test_a"
    assert elapsed < 30, f"find_ways took {elapsed:.1f}s on the shard layout"
    print(f"find_ways on the v2 layout ({'search group' if _grouped else 'one search db'}): {elapsed:.2f}s")
