"""Live-DB tests: Goal identity (FTS+vector candidates -> model judge), durable
decisions, Procedure -> Goal direct linking, concurrency and replay."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services.goals import find_or_create_goal
from app.services.procedures import capture_procedure, supersede_procedure
from app.services.semantic.errors import ErrorKind, ProviderError, SemanticJudgmentUnavailable
from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
P = "gid"  # name prefix for cleanup
KW = dict(scope_type="global", provenance="prior_library", created_from="test")


def n(name: str) -> str:
    return f"{P} {name}"


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{P} %")
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{P} %")
    await p.execute("DELETE FROM goal_relations WHERE specific_goal_id IN (SELECT id FROM goals WHERE canonical_name LIKE $1)", f"{P} %")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{P} %")
    await p.close()


async def _goal(pool, name, judge, emb, **kw):
    return await find_or_create_goal(pool, canonical_name=n(name), embedder=emb, judge=judge, **{**KW, **kw})


@pytest.mark.asyncio
async def test_exact_duplicate_is_deterministic_and_never_calls_the_judge(pool):
    prov = FrozenProvider({})
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers of a function", j, e)
    b = await _goal(pool, "Find callers of a FUNCTION!", j, e)
    assert a["created"] and not b["created"] and a["id"] == b["id"]
    assert prov.calls == []


@pytest.mark.asyncio
async def test_paraphrase_is_merged_only_because_the_judge_said_same(pool):
    prov = FrozenProvider({(n("locate every call site of a function"), n("find callers of a function")): ("same", 0.93)})
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers of a function", j, e)
    b = await _goal(pool, "locate every call site of a function", j, e)
    assert not b["created"] and b["id"] == a["id"] and b["decision"] == "same"
    assert len(prov.calls) == 1                                     # the model decided, once
    row = await pool.fetchrow("SELECT * FROM identity_decisions WHERE resolved_id=$1::uuid", a["id"])
    assert row["decision"] == "same" and row["judge_provider"] == "frozen-jev" and row["fts_candidates"] + row["vector_candidates"] >= 1
    assert row["prompt_version"] == "identity@v2" and row["candidates"]
    assert n("locate every call site of a function") in await pool.fetchval("SELECT aliases FROM goals WHERE id=$1::uuid", a["id"])


@pytest.mark.asyncio
async def test_lexically_similar_but_distinct_goal_is_not_merged(pool):
    prov = FrozenProvider({(n("find callers and delete them"), n("find callers of a function")): ("distinct", 0.97)})
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers of a function", j, e)
    b = await _goal(pool, "find callers and delete them", j, e)
    assert b["created"] and b["id"] != a["id"]
    assert (await pool.fetchval("SELECT decision FROM identity_decisions WHERE object_type='goal' AND candidate_text LIKE $1", f"{n('find callers and delete')}%")) == "distinct"


@pytest.mark.asyncio
async def test_high_embedding_similarity_alone_never_merges(pool):
    """Old behaviour: cosine <= 0.12 auto-merged. Now the judge says distinct -> two goals."""
    prov = FrozenProvider({}, default=("distinct", 0.9))
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers", j, e)
    b = await _goal(pool, "locate callers", j, e)   # identical concept vectors (cosine ~ 1.0)
    assert a["id"] != b["id"] and b["created"]


@pytest.mark.asyncio
async def test_low_confidence_same_is_not_a_merge(pool):
    prov = FrozenProvider({(n("discover callers"), n("find callers")): ("same", 0.4)})
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers", j, e)
    b = await _goal(pool, "discover callers", j, e)
    assert b["created"] and b["id"] != a["id"]


@pytest.mark.asyncio
async def test_narrower_goal_creates_goal_and_a_separate_optional_goal_relation(pool):
    prov = FrozenProvider({(n("find callers of a python function"), n("find callers")): ("specializes", 0.9)})
    j, e = make_judge(prov), ConceptEmbedder()
    general = await _goal(pool, "find callers", j, e)
    specific = await _goal(pool, "find callers of a python function", j, e)
    assert specific["created"] and specific["id"] != general["id"]
    rel = await pool.fetchrow("SELECT * FROM goal_relations WHERE specific_goal_id=$1::uuid", specific["id"])
    assert str(rel["abstract_goal_id"]) == general["id"] and rel["relation_type"] == "SPECIALIZES" and rel["status"] == "proposed"
    # hierarchy does not affect physical placement fields
    assert (await pool.fetchval("SELECT home_shard_id FROM goals WHERE id=$1::uuid", specific["id"])) == "K000"


@pytest.mark.asyncio
async def test_judge_outage_with_candidates_fails_closed_then_dev_mode_creates_and_audits(pool):
    ok = FrozenProvider({})
    j_ok, e = make_judge(ok), ConceptEmbedder()
    await _goal(pool, "find callers", j_ok, e)
    down = FrozenProvider({}, fail=lambda: ProviderError(ErrorKind.TRANSIENT, "503"))
    j_down = make_judge(down)
    before = await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1", f"{P} %")
    with pytest.raises(SemanticJudgmentUnavailable):
        await _goal(pool, "discover callers", j_down, e)
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1", f"{P} %") == before   # nothing written
    created = await _goal(pool, "discover callers", j_down, e, on_unavailable="create")
    assert created["created"]
    assert await pool.fetchval("SELECT decision FROM identity_decisions WHERE resolved_id IS NULL AND candidate_text LIKE $1", f"{n('discover callers')}%") == "judge_unavailable"


@pytest.mark.asyncio
async def test_embedding_provider_failure_propagates_and_writes_nothing(pool):
    j = make_judge(FrozenProvider({}))
    with pytest.raises(RuntimeError):
        await _goal(pool, "brand new goal", j, ConceptEmbedder(fail=True))
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name=$1", n("brand new goal")) == 0


@pytest.mark.asyncio
async def test_replayed_job_reuses_its_earlier_same_decision_without_rejudging(pool):
    prov = FrozenProvider({(n("locate callers of a function"), n("find callers of a function")): ("same", 0.9)})
    j, e = make_judge(prov), ConceptEmbedder()
    a = await _goal(pool, "find callers of a function", j, e)
    r1 = await _goal(pool, "locate callers of a function", j, e, idempotency_key="job-1:g")
    calls = len(prov.calls)
    # simulate the retry after a crash: drop the alias that the first attempt appended
    await pool.execute("UPDATE goals SET aliases='{}' WHERE id=$1::uuid", a["id"])
    r2 = await _goal(pool, "locate callers of a function", j, e, idempotency_key="job-1:g")
    assert r1["id"] == r2["id"] == a["id"] and len(prov.calls) == calls
    assert await pool.fetchval("SELECT count(*) FROM identity_decisions WHERE idempotency_key='job-1:g'") == 1


@pytest.mark.asyncio
async def test_concurrent_identical_ingestion_creates_exactly_one_goal(pool):
    j, e = make_judge(FrozenProvider({})), ConceptEmbedder()
    results = await asyncio.gather(*[_goal(pool, "deploy the service safely", j, e) for _ in range(10)])
    assert len({r["id"] for r in results}) == 1
    assert sum(1 for r in results if r["created"]) == 1


@pytest.mark.asyncio
async def test_procedures_from_different_sources_link_directly_to_the_one_goal(pool):
    prov = FrozenProvider({(n("locate every call site of a function"), n("find callers of a function")): ("same", 0.95)})
    j, e = make_judge(prov), ConceptEmbedder()
    kw = dict(provenance="prior_library", scope_type="global", goal_embedder=e, goal_judge=j)
    p1 = await capture_procedure(pool, name=n("proc grep"), goal=n("find callers of a function"), steps=[{"description": "rg foo"}], **kw)
    p2 = await capture_procedure(pool, name=n("proc lsp"), goal=n("locate every call site of a function"), steps=[{"description": "lsp refs"}], **kw)
    r = await pool.fetch("SELECT id, procedure_id, achieves_goal_id, home_shard_id FROM procedures WHERE name LIKE $1", f"{P} proc%")
    assert len(r) == 2                                   # different methods stay separate procedures
    assert len({x["achieves_goal_id"] for x in r}) == 1 and r[0]["achieves_goal_id"] is not None
    assert {x["home_shard_id"] for x in r} == {"K000"}
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1", f"{P} %") == 1
    # new VERSION keeps the direct Goal link (previously supersede dropped it)
    v2 = await supersede_procedure(pool, prior_row_id=p1["id"], changed_fields={"steps": [{"description": "rg -n foo"}]})
    link = await pool.fetchval("SELECT achieves_goal_id FROM procedures WHERE id=$1::uuid", v2["id"])
    assert str(link) == str(r[0]["achieves_goal_id"])
