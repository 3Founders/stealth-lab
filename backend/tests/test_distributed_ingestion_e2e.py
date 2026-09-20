"""Distributed-ingestion failure suite (live DB). Every test ends by proving that
NO duplicate canonical objects exist, whatever crashed or was delivered twice."""
import asyncio
import os
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.ingestion import queue as q
from app.ingestion.config import WorkerConfig
from app.ingestion.handlers import JOB_TYPE, Dependencies, handle_ingest_candidate_bundle
from app.ingestion.worker import Worker, is_retryable
from app.services import search_projection as sp
from app.services.semantic.errors import ErrorKind, ProviderError, SemanticJudgmentUnavailable
from app.services.shards import ShardUnavailable
from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
T = "dist"
CFG = WorkerConfig(concurrency=1, lease_seconds=60, retry_base_seconds=0.0, retry_cap_seconds=0.0, poll_seconds=0.05,
                   drain_projections=True)


def n(s):
    return f"{T} {s}"


G1, G1P = n("find callers of a function"), n("locate every call site of a function")


def judge(fail=None):
    return make_judge(FrozenProvider({(G1P, G1): ("same", 0.95)}, fail=fail))


def bundle(key, goal=G1, proc="grep callers", claims=()):
    return {"source_key": f"{T}-{key}", "goal": goal, "procedure": {"name": n(proc), "steps": [{"description": proc}]},
            "claims": [{"statement": c} for c in claims], "provenance": "prior_library"}


async def enq(pool, b, *, key=None, scope="global", visibility="public", owner=None, entity=None):
    return await q.enqueue(pool, JOB_TYPE, b, idempotency_key=key or f"idem-{b['source_key']}", source_id=b["source_key"],
                           scope_type=scope, scope_entity_id=entity, visibility=visibility, owner_id=owner)


async def make_due(pool):
    await pool.execute("UPDATE ingestion_jobs SET run_after = now() WHERE status = 'retryable_failed'")


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    await p.execute("DELETE FROM ingestion_jobs WHERE job_type = $1 OR idempotency_key IN ('nohandler', 'bad-payload')", JOB_TYPE)
    Dependencies.configure(embedder=ConceptEmbedder(), judge=judge())
    yield p
    Dependencies.configure()
    await p.execute("DELETE FROM ingestion_jobs WHERE job_type = $1 OR idempotency_key IN ('nohandler', 'bad-payload')", JOB_TYPE)
    await p.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{T} %")
    await p.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{T} %")
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{T} %")
    await p.execute("DELETE FROM goal_relations WHERE specific_goal_id IN (SELECT id FROM goals WHERE canonical_name LIKE $1)", f"{T} %")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T} %")
    await p.execute("DELETE FROM goal_search_index WHERE canonical_name LIKE $1", f"{T} %")
    await p.execute("DELETE FROM procedure_search_index WHERE name LIKE $1", f"{T} %")
    await p.execute("DELETE FROM claim_search_index WHERE statement LIKE $1", f"{T} %")
    await p.execute("DELETE FROM ingestion_contexts WHERE source_uri LIKE $1", f"bundle:{T}-%")
    await p.close()


async def counts(pool):
    return dict(
        goals=await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1 AND status <> 'merged'", f"{T} %"),
        procs=await pool.fetchval("SELECT count(*) FROM procedures WHERE name LIKE $1 AND t_invalid IS NULL", f"{T} %"),
        claims=await pool.fetchval("SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T} %"),
        ctx=await pool.fetchval("SELECT count(*) FROM ingestion_contexts WHERE source_uri LIKE $1", f"bundle:{T}-%"),
    )


def worker(pool, wid, **over):
    return Worker(pool, replace(CFG, **over), worker_id=wid)


# ---------------------------------------------------------------- queue semantics


@pytest.mark.asyncio
async def test_duplicate_enqueue_is_idempotent(pool):
    a = await enq(pool, bundle("s1"))
    b = await enq(pool, bundle("s1"))
    assert a[1] is True and b[1] is False and a[0] == b[0]
    assert await pool.fetchval("SELECT count(*) FROM ingestion_jobs WHERE job_type=$1", JOB_TYPE) == 1


@pytest.mark.asyncio
async def test_two_workers_never_lease_the_same_job(pool):
    for i in range(20):
        await enq(pool, bundle(f"lease{i}", goal=n(f"goal number {i}")))
    a, b = await asyncio.gather(q.lease(pool, "A", limit=20, job_types=[JOB_TYPE]), q.lease(pool, "B", limit=20, job_types=[JOB_TYPE]))
    ids = [j.id for j in a] + [j.id for j in b]
    assert len(ids) == len(set(ids)) == 20


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_the_zombie_is_fenced(pool):
    await enq(pool, bundle("zombie"))
    (a,) = await q.lease(pool, "A", lease_seconds=60)
    assert await q.lease(pool, "B") == []                      # A holds a live lease
    await pool.execute("UPDATE ingestion_jobs SET lease_until = now() - interval '1 second' WHERE id=$1", a.id)   # A "died"
    (b,) = await q.lease(pool, "B")
    assert b.id == a.id and b.attempt == 2
    assert await q.complete(pool, a) is False                  # zombie A wakes up: fenced out
    assert await q.heartbeat(pool, a, 60) is False
    assert await q.fail(pool, a, "late", retryable=True) == "lost"
    assert await q.complete(pool, b) is True
    assert await pool.fetchval("SELECT status FROM ingestion_jobs WHERE id=$1", a.id) == "done"


@pytest.mark.asyncio
async def test_crash_loop_is_stopped_after_max_attempts(pool):
    jid, _ = await q.enqueue(pool, JOB_TYPE, bundle("loop"), idempotency_key="loop", scope_type="global", max_attempts=2)
    for _ in range(2):
        await q.lease(pool, "W")
        await pool.execute("UPDATE ingestion_jobs SET lease_until = now() - interval '1 second' WHERE id=$1", jid)
    assert await q.lease(pool, "W") == []                       # attempts exhausted: never leased again
    assert await q.reap_exhausted(pool) == 1
    assert await pool.fetchval("SELECT status FROM ingestion_jobs WHERE id=$1", jid) == "failed"


# ------------------------------------------------------------ canonical idempotency


@pytest.mark.asyncio
async def test_same_source_delivered_twice_creates_one_of_everything(pool):
    b = bundle("dup", claims=[f"{T} callers are found by name"])
    await enq(pool, b, key="delivery-1")
    await enq(pool, b, key="delivery-2")                        # different job, same source
    await worker(pool, "A").run(loop=False)
    c = await counts(pool)
    assert c == dict(goals=1, procs=1, claims=1, ctx=1)
    assert await pool.fetchval("SELECT count(*) FROM ingestion_jobs WHERE job_type=$1 AND status='done'", JOB_TYPE) == 2


@pytest.mark.asyncio
async def test_concurrent_workers_on_overlapping_sources_create_no_duplicates(pool):
    # 6 sources; each delivered 3 times; two goals that are paraphrases; two workers with 3 lanes each
    for s in range(6):
        goal = G1 if s % 2 == 0 else G1P
        for d in range(3):
            await enq(pool, bundle(f"race{s}", goal=goal, proc=f"method {s}", claims=[f"{T} fact {s}"]), key=f"race{s}-{d}")
    await asyncio.gather(worker(pool, "A", concurrency=3).run(loop=False), worker(pool, "B", concurrency=3).run(loop=False))
    c = await counts(pool)
    assert c["goals"] == 1                                       # paraphrase resolved to ONE goal by the judge
    assert c["procs"] == 6 and c["claims"] == 6 and c["ctx"] == 6
    goal_links = await pool.fetch("SELECT DISTINCT achieves_goal_id FROM procedures WHERE name LIKE $1", f"{T} %")
    assert len(goal_links) == 1 and goal_links[0]["achieves_goal_id"] is not None
    assert await pool.fetchval("SELECT count(*) FROM ingestion_jobs WHERE job_type=$1 AND status='done'", JOB_TYPE) == 18


@pytest.mark.asyncio
async def test_worker_dies_after_extraction_before_persistence(pool):
    await enq(pool, bundle("dies-early"))
    (a,) = await q.lease(pool, "A")                             # A leased, "extracted", then died: nothing persisted
    assert (await counts(pool))["procs"] == 0
    await pool.execute("UPDATE ingestion_jobs SET lease_until = now() - interval '1 second' WHERE id=$1", a.id)
    await worker(pool, "B").run(loop=False)
    assert (await counts(pool)) == dict(goals=1, procs=1, claims=0, ctx=1)


@pytest.mark.asyncio
async def test_worker_dies_after_canonical_persistence_before_projection_and_completion(pool):
    await sp.drain_outbox(pool)
    await enq(pool, bundle("dies-late", claims=[f"{T} x is true"]))
    (a,) = await q.lease(pool, "A")
    await handle_ingest_candidate_bundle(pool, {**a.payload, "_job": {"id": a.id, "scope_type": "global", "visibility": "public"}})
    # A dies here: canonical rows exist, projection NOT applied, job still 'processing'
    rep = await sp.verify_projection(pool)
    assert rep["lag"]["pending"] >= 1 and rep["types"]["procedure"]["lagging"] >= 1     # visible, not silent
    assert await pool.fetchval("SELECT count(*) FROM procedure_search_index WHERE name LIKE $1", f"{T} %") == 0
    await pool.execute("UPDATE ingestion_jobs SET lease_until = now() - interval '1 second' WHERE id=$1", a.id)
    await worker(pool, "B").run(loop=False)                     # retry re-runs the handler + drains the outbox
    assert (await counts(pool)) == dict(goals=1, procs=1, claims=1, ctx=1)               # replay created nothing new
    assert await pool.fetchval("SELECT count(*) FROM procedure_search_index WHERE name LIKE $1", f"{T} %") == 1
    assert await pool.fetchval("SELECT count(*) FROM claim_search_index WHERE statement LIKE $1", f"{T} %") == 1
    assert (await sp.verify_projection(pool))["ok"]


@pytest.mark.asyncio
async def test_embedding_provider_temporary_failure_retries_and_converges(pool):
    class Flaky(ConceptEmbedder):
        async def embed_one(self, text, input_type="document"):
            self.calls += 1
            if self.calls <= 2:
                raise ConnectionError("embedding provider 503")
            return await super().embed_one(text, input_type)

    Dependencies.configure(embedder=Flaky(), judge=judge())
    await enq(pool, bundle("emb"))
    w = worker(pool, "A", retry_base_seconds=3600.0, retry_cap_seconds=3600.0)      # real backoff: not retried in this run
    r1 = await w.run(loop=False)
    assert r1["retryable_failed"] == 1 and (await counts(pool))["procs"] == 0
    for _ in range(3):
        await make_due(pool)
        await w.run(loop=False)
    row = await pool.fetchrow("SELECT status, attempts, last_error FROM ingestion_jobs WHERE job_type=$1", JOB_TYPE)
    assert row["status"] == "done" and row["attempts"] >= 2
    assert (await counts(pool)) == dict(goals=1, procs=1, claims=0, ctx=1)


@pytest.mark.asyncio
async def test_judge_temporary_failure_fails_closed_then_resolves_without_a_duplicate_goal(pool):
    await enq(pool, bundle("j0"))
    await worker(pool, "A").run(loop=False)                     # seeds G1 with a healthy judge
    outage = {"on": True}

    def fail():
        return ProviderError(ErrorKind.TRANSIENT, "jev 503") if outage["on"] else None

    Dependencies.configure(embedder=ConceptEmbedder(), judge=judge(fail=fail))
    await enq(pool, bundle("j1", goal=G1P, proc="lsp references"))
    w = worker(pool, "B", retry_base_seconds=3600.0, retry_cap_seconds=3600.0)
    r = await w.run(loop=False)
    assert r["retryable_failed"] == 1
    assert (await counts(pool))["goals"] == 1 and (await counts(pool))["procs"] == 1       # nothing half-written
    outage["on"] = False
    await make_due(pool)
    await w.run(loop=False)
    assert (await counts(pool)) == dict(goals=1, procs=2, claims=0, ctx=2)                # merged into the ONE goal
    assert await pool.fetchval("SELECT decision FROM identity_decisions WHERE object_type='goal' AND decision='same'") == "same"


@pytest.mark.asyncio
async def test_permanent_failures_are_not_retried(pool):
    bad = {"goal": G1, "procedure": {"name": n("p"), "steps": []}}                          # missing source_key
    await q.enqueue(pool, JOB_TYPE, bad, idempotency_key="bad-payload", scope_type="global")
    await q.enqueue(pool, "no_such_job_type", {}, idempotency_key="nohandler", scope_type="global")
    r = await worker(pool, "A").run(loop=False)
    assert r["failed"] == 2 and r["retryable_failed"] == 0
    rows = await pool.fetch("SELECT status, attempts FROM ingestion_jobs WHERE idempotency_key IN ('bad-payload','nohandler')")
    assert {(x["status"], x["attempts"]) for x in rows} == {("failed", 1)}


def test_failure_classification():
    assert is_retryable(SemanticJudgmentUnavailable("x")) and is_retryable(ShardUnavailable("K1", "down"))
    assert is_retryable(ConnectionError()) and is_retryable(asyncio.TimeoutError())
    assert not is_retryable(KeyError("source_key")) and not is_retryable(ValueError("bad")) and not is_retryable(q.ScopeError("x"))


# ------------------------------------------------------------------- scope / privacy


@pytest.mark.asyncio
async def test_public_only_job_types_refuse_private_scope_and_bundles_keep_their_scope(pool):
    with pytest.raises(q.ScopeError):
        await q.enqueue(pool, "ingest_skill_package", {"x": 1}, idempotency_key="k", scope_type="user", visibility="private", owner_id="a")
    with pytest.raises(q.ScopeError):
        await q.enqueue(pool, JOB_TYPE, {}, idempotency_key="k2", scope_type="user", visibility="private")     # no owner
    await enq(pool, bundle("priv", goal=n("private roadmap"), claims=[f"{T} secret"]), scope="user", entity="alice", visibility="private", owner="alice")
    await worker(pool, "A").run(loop=False)
    assert await pool.fetchval("SELECT visibility::text FROM procedures WHERE name LIKE $1", f"{T} %") == "private"
    assert await pool.fetchval("SELECT visibility::text FROM goals WHERE canonical_name LIKE $1", f"{T} %") == "private"
    assert await pool.fetchval("SELECT visibility::text FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T} %") == "private"
    assert await pool.fetchval("SELECT visibility::text FROM procedure_search_index WHERE name LIKE $1", f"{T} %") == "private"   # projection does not publish it
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE canonical_name LIKE $1 AND visibility='public'", f"{T} %") == 0


@pytest.mark.asyncio
async def test_procedure_identity_same_refinement_and_alternative(pool):
    """Procedures for ONE goal: same method (other source) is reused, refinement is a new version, alternative stays separate."""
    p_grep, p_grep2, p_grep3, p_lsp = n("grep callers"), n("grep for callers"), n("grep callers -n"), n("lsp references")
    prov = FrozenProvider({(p_grep2 + ": " + G1, p_grep + ": " + G1): ("same", 0.9)})

    def verdict(kind, a, b):
        return None
    from tests.identity_fakes import CallbackProvider

    def fn(kind, a, b):
        if kind == "procedure":
            if "grep for callers" in a and "grep callers" in b:
                return ("same", 0.9)
            if "-n" in a and "grep callers" in b:
                return ("refinement", 0.9)
            return ("distinct", 0.9)
        return ("same", 0.95) if kind == "goal" and "call site" in a else ("distinct", 0.9)

    j = make_judge(CallbackProvider(fn, name="jev"))
    Dependencies.configure(embedder=ConceptEmbedder(), judge=j)
    for key, name in (("a", "grep callers"), ("b", "grep for callers"), ("c", "lsp references")):
        await enq(pool, bundle(key, proc=name))
    await worker(pool, "A").run(loop=False)
    live = await pool.fetch("SELECT name FROM procedures WHERE name LIKE $1 AND t_invalid IS NULL ORDER BY name", f"{T} %")
    assert [r["name"] for r in live] == sorted([p_grep, p_lsp])                # 'grep for callers' was the SAME method -> not duplicated
    refs = await pool.fetchval("SELECT evidence_refs FROM procedures WHERE name=$1", p_grep)
    assert any(r.get("source_key") == f"{T}-b" for r in refs)                 # the second source is attached as provenance
    await enq(pool, bundle("d", proc="grep callers -n"))
    await worker(pool, "A").run(loop=False)
    versions = await pool.fetch("SELECT version, t_invalid FROM procedures WHERE name LIKE '%grep callers%' OR name LIKE '%grep callers -n%' ORDER BY version")
    assert max(v["version"] for v in versions) == 2                           # refinement -> new VERSION of the same procedure
    assert (await counts(pool))["goals"] == 1
