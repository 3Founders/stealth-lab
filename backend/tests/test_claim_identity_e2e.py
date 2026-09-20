"""Claim identity (live DB): same proposition reused with provenance, contradiction kept, privacy classes
never mixed, concurrency, outage policy, remote shard."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.claim_identity import ingest_claim
from app.services.semantic.errors import ErrorKind, ProviderError, SemanticJudgmentUnavailable
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
T = "clm" + uuid.uuid4().hex[:5]
EMB = ConceptEmbedder()


def s_(x):
    return f"{T} {x}"


def verdict(kind, a, b):
    al, bl = a.lower(), b.lower()
    if kind != "claim" or T not in bl:
        return ("distinct", .9)
    if ("does not" in al) != ("does not" in bl) and "cache" in al + bl:
        return ("contradicts", .9)
    if "cache" in al and "cache" in bl:
        if "maybe" in al or "maybe" in bl:
            return ("same", .6)           # low confidence: must not merge
        return ("same", .95)
    return ("distinct", .9)


def judge(fail=False):
    return make_judge(CallbackProvider(verdict, name="jev", fail=(lambda k: ProviderError(ErrorKind.TRANSIENT, "503")) if fail else None))


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM claim_relation_candidates WHERE claim_a_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1) OR claim_b_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{T}%")
    await p.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{T}%")
    await p.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{T}%")
    await p.execute("DELETE FROM claim_search_index WHERE statement LIKE $1", f"{T}%")
    await p.close()


async def ing(pool, text, src, **kw):
    r = await ingest_claim(pool, statement=s_(text), scope_type="global", source_key=f"{T}-{src}", source_ref=f"{T}-{src}",
                           embedder=EMB, judge=kw.pop("judge", judge()), **kw)
    await sp.drain_outbox(pool)
    return r


@pytest.mark.asyncio
async def test_exact_and_paraphrase_are_one_claim_and_provenance_accumulates(pool):
    a = await ing(pool, "the cache is enabled by default", "s1")
    b = await ing(pool, "The   cache is enabled by default", "s2")               # exact after normalisation
    c = await ing(pool, "caches are on unless you opt out", "s3")                 # paraphrase, judged same
    assert a["action"] == "created" and b["action"] == c["action"] == "reused"
    assert {a["claim_id"]} == {b["claim_id"], c["claim_id"]}
    refs = await pool.fetchval("SELECT properties->'source_refs' FROM knowledge_nodes WHERE id=$1::uuid", a["claim_id"])
    assert {r["source_key"] for r in refs} == {f"{T}-s1", f"{T}-s2", f"{T}-s3"}          # every source kept
    assert await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T}%") == 1


@pytest.mark.asyncio
async def test_a_contradiction_is_never_merged(pool):
    a = await ing(pool, "the cache is enabled by default", "s1")
    b = await ing(pool, "the cache does not run by default", "s2")
    assert b["action"] == "created" and b["claim_id"] != a["claim_id"] and b["decision"] == "contradicts"
    row = await pool.fetchrow("SELECT relation, status FROM claim_relation_candidates WHERE (claim_a_id=$1::uuid AND claim_b_id=$2::uuid) "
                              "OR (claim_a_id=$2::uuid AND claim_b_id=$1::uuid)", a["claim_id"], b["claim_id"])
    assert row["relation"] == "contradicts" and row["status"] == "pending"                 # queued for review, nothing auto-resolved
    assert a["claim_id"] in [r["claim_id"] for r in b["related"]]


@pytest.mark.asyncio
async def test_low_confidence_same_is_not_a_merge(pool):
    a = await ing(pool, "the cache is enabled by default", "s1")
    b = await ing(pool, "maybe the cache is on", "s2")
    assert b["action"] == "created" and b["claim_id"] != a["claim_id"]


@pytest.mark.asyncio
async def test_private_and_public_claims_are_never_compared_or_mixed(pool):
    pub = await ing(pool, "the cache is enabled by default", "pub")
    priv = await ing(pool, "the cache is enabled by default", "mine", visibility="private", owner_id="alice")
    assert priv["action"] == "created" and priv["claim_id"] != pub["claim_id"]
    other = await ing(pool, "the cache is enabled by default", "bob", visibility="private", owner_id="bob")
    assert other["claim_id"] not in (pub["claim_id"], priv["claim_id"])
    refs = await pool.fetchval("SELECT properties->'source_refs' FROM knowledge_nodes WHERE id=$1::uuid", pub["claim_id"])
    assert {r["source_key"] for r in refs} == {f"{T}-pub"}                                   # no private source leaked onto the public row


@pytest.mark.asyncio
async def test_replay_of_the_same_source_is_idempotent(pool):
    a = await ing(pool, "the cache is enabled by default", "s1")
    await ing(pool, "caches are on unless you opt out", "s2")
    again = await ing(pool, "caches are on unless you opt out", "s2")
    assert again["action"] == "reused" and again["claim_id"] == a["claim_id"]
    assert await pool.fetchval("SELECT count(*) FROM identity_decisions WHERE object_type='claim' AND candidate_text LIKE $1", f"{T}%") >= 1


@pytest.mark.asyncio
async def test_concurrent_paraphrases_of_one_proposition_create_one_claim(pool):
    texts = ["the cache is enabled by default", "caches are on unless you opt out", "cache default: enabled", "by default the cache is on"]
    res = await asyncio.gather(*[ing(pool, t, f"w{i}") for i, t in enumerate(texts * 2)])
    assert len({r["claim_id"] for r in res}) == 1
    assert await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T}%") == 1


@pytest.mark.asyncio
async def test_judge_outage_fails_closed_for_public_and_creates_for_private(pool):
    await ing(pool, "the cache is enabled by default", "s1")
    with pytest.raises(SemanticJudgmentUnavailable):
        await ing(pool, "caches are on unless you opt out", "s2", judge=judge(fail=True))
    assert await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T}%") == 1
    await ing(pool, "the cache is enabled by default", "p0", visibility="private", owner_id="alice")
    r = await ing(pool, "caches are on unless you opt out", "p1", visibility="private", owner_id="alice", judge=judge(fail=True))
    assert r["action"] == "created" and r["decision"] == "judge_unavailable"                  # a private/local write never blocks


SHARD_DSN = os.environ.get("TEST_SHARD_DATABASE_URL")


@pytest.mark.skipif(not SHARD_DSN, reason="needs TEST_SHARD_DATABASE_URL")
@pytest.mark.asyncio
async def test_claims_dedup_across_shards(pool):
    shard = await create_pool(SHARD_DSN)
    os.environ["K001_DATABASE_URL"] = SHARD_DSN
    await sh.register_shard(pool, "K001", dsn_env="K001_DATABASE_URL", weight=100)
    await sh.register_shard(pool, "K000", dsn_env=None, weight=0)
    try:
        a = await ing(pool, "the cache is enabled by default", "s1")
        assert a["home_shard_id"] == "K001"
        assert await shard.fetchval("SELECT count(*) FROM knowledge_nodes WHERE id=$1::uuid", a["claim_id"]) == 1
        assert await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE id=$1::uuid", a["claim_id"]) == 0
        b = await ing(pool, "caches are on unless you opt out", "s2")                     # candidate found through the projection
        assert b["action"] == "reused" and b["claim_id"] == a["claim_id"]
        refs = await shard.fetchval("SELECT properties->'source_refs' FROM knowledge_nodes WHERE id=$1::uuid", a["claim_id"])
        assert len(refs) == 2                                                             # provenance written on the claim's own shard
    finally:
        await shard.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{T}%")
        await pool.execute("DELETE FROM object_routes WHERE home_shard_id='K001'")
        await pool.execute("DELETE FROM claim_search_index WHERE home_shard_id='K001'")
        await sh.register_shard(pool, "K000", dsn_env=None, weight=100)
        await pool.execute("DELETE FROM knowledge_shards WHERE shard_id='K001'")
        sh.invalidate_shard_cache()
        await shard.close()
