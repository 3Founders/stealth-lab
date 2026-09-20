"""END-TO-END acceptance: enqueue -> concurrent workers -> canonical objects ->
projections -> Tier-1 goal retrieval -> Tier-2 procedure retrieval -> hydration ->
existing execution/plan path. Every step goes through production code; nothing is
a hand-seeded "final result".

Model seams (embedder, JEV/NLI) are frozen fixtures; see test_retrieval_live_models.py
for the optional live-provider variant.
"""
import asyncio
import os
from dataclasses import replace
from uuid import UUID

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.ingestion import queue as q
from app.ingestion.config import WorkerConfig
from app.ingestion.handlers import JOB_TYPE, Dependencies
from app.ingestion.worker import Worker
from app.services import domain_search as ds
from app.services import identity_resolution as ir
from app.services import search_projection as sp
from app.services.access import AccessScope
from app.services.procedures import record_execution_outcome
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
import uuid as _uuid

# Unique per run: execution_plans are append-only (they FK the procedure), so this test
# cannot delete what it created. Run it on a disposable database (as every row-writing e2e here).
T = "e2e" + _uuid.uuid4().hex[:6]
CFG = WorkerConfig(concurrency=3, lease_seconds=60, retry_base_seconds=0.0, poll_seconds=0.05, drain_projections=True)
EMB = ConceptEmbedder()

GOAL = f"{T} find callers of a function"
GOAL_PARA = f"{T} locate every call site of a function"
GOAL_NARROW = f"{T} find callers of a python function"
GOAL_OTHER = f"{T} deploy the service safely"


def frozen(kind, a, b):
    al, bl = a.lower(), b.lower()
    if T not in bl:                                               # rows left by earlier runs are never related to this run
        return {"goal": ("distinct", 0.9), "procedure": ("distinct", 0.9), "task_goal": ("unrelated", 0.9),
                "task_procedure": ("not_applicable", 0.9), "claim": ("distinct", 0.9)}[kind]
    if kind == "claim":
        return ("distinct", 0.9)
    if kind == "goal":                                            # ingestion-time identity
        if "call site" in al and "find callers of a function" in bl and "python" not in bl:
            return ("same", 0.95)
        if "find callers of a function" in al and "call site" in bl and "python" not in al:      # symmetric verdict
            return ("same", 0.95)
        base_phrasing = ("find callers of a function", "call site of a function")     # either phrasing of the base goal
        if "python function" in al and any(x in bl for x in base_phrasing):
            return ("specializes", 0.9)
        if any(x in al for x in base_phrasing) and "python function" in bl:            # the same pair judged from the other side
            return ("generalizes", 0.9)
        return ("distinct", 0.9)
    if kind == "procedure":
        return ("distinct", 0.9)
    q_ = al.split("\nknown in this environment")[0]
    if kind == "task_goal":
        if "deploy" in bl:
            return ("matches", 0.9) if "deploy" in q_ else ("unrelated", 0.9)
        if "python" in bl and "python" not in q_:
            return ("unrelated", 0.9)
        return ("matches", 0.9) if ("caller" in q_ or "call site" in q_) and "deploy" not in bl else ("unrelated", 0.9)
    if kind == "task_procedure":
        return ("not_applicable", 0.9) if ("bazel" in bl and "gradle" in al) else ("applies", 0.9)
    raise AssertionError(kind)


def bundle(key, goal, proc, claims=()):
    return {"source_key": f"{T}-{key}", "source_uri": f"https://example.test/{T}/{key}", "goal": goal,
            "procedure": {"name": f"{T} {proc}", "steps": [{"description": proc}]},
            "claims": [{"statement": f"{T} {c}"} for c in claims], "provenance": "prior_library"}


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    # tombstone (the schema's own retire idiom) what earlier runs left behind: it cannot be deleted (append-only plans)
    await p.execute("UPDATE procedures SET t_invalid = now() WHERE name LIKE 'e2e%' AND t_invalid IS NULL")
    await p.execute("UPDATE goals SET t_invalid = now() WHERE canonical_name LIKE 'e2e%' AND t_invalid IS NULL")
    await p.execute("DELETE FROM ingestion_jobs WHERE job_type = $1", JOB_TYPE)
    # projection rows whose canonical row another test deleted are orphans: drop them so verify_projection is about THIS run
    await p.execute("DELETE FROM goal_search_index WHERE goal_id NOT IN (SELECT id FROM goals)")
    await p.execute("DELETE FROM procedure_search_index WHERE procedure_id NOT IN (SELECT procedure_id FROM procedures)")
    await p.execute("DELETE FROM claim_search_index WHERE claim_id NOT IN (SELECT id FROM knowledge_nodes)")
    judge = make_judge(CallbackProvider(frozen, name="jev"))
    Dependencies.configure(embedder=EMB, judge=judge)
    ir._DEFAULT_JUDGE = judge                                     # retrieval service resolves its judge the production way
    yield p
    ir.reset_default_judge()
    Dependencies.configure()
    await p.execute("DELETE FROM ingestion_jobs WHERE job_type = $1", JOB_TYPE)
    # leave nothing live behind (plans are append-only so rows cannot be deleted): other suites must not see this run's goals
    await p.execute("UPDATE procedures SET t_invalid = now() WHERE name LIKE 'e2e%' AND t_invalid IS NULL")
    await p.execute("UPDATE goals SET t_invalid = now() WHERE canonical_name LIKE 'e2e%' AND t_invalid IS NULL")
    await p.execute("DELETE FROM goal_search_index WHERE canonical_name LIKE 'e2e%'")
    await p.execute("DELETE FROM procedure_search_index WHERE name LIKE 'e2e%'")
    await p.close()


@pytest.mark.asyncio
async def test_full_pipeline_from_enqueue_to_plan(pool):
    # 1. enqueue sources (one delivered twice)
    sources = [
        bundle("grep", GOAL, "grep callers", ["callers are found by name", "grep is available"]),
        bundle("lsp", GOAL_PARA, "lsp find references", ["the language server is running"]),   # paraphrased goal
        bundle("bazel", GOAL, "bazel query rdeps", ["bazel builds this repo"]),
        bundle("py", GOAL_NARROW, "jedi usages"),                                             # narrower goal
        bundle("deploy", GOAL_OTHER, "canary rollout"),                                       # unrelated goal
    ]
    for s in sources:
        await q.enqueue(pool, JOB_TYPE, s, idempotency_key=f"e2e-{s['source_key']}", source_id=s["source_key"], scope_type="global")
    await q.enqueue(pool, JOB_TYPE, sources[0], idempotency_key="e2e-redelivery", source_id="grep", scope_type="global")

    # 2. several independent workers race over the queue
    workers = [Worker(pool, replace(CFG), worker_id=f"w{i}") for i in range(3)]
    results = await asyncio.gather(*[w.run(loop=False) for w in workers])
    assert sum(r["done"] for r in results) == 6 and sum(r["failed"] + r["retryable_failed"] for r in results) == 0

    # 3. canonical state
    goals = await pool.fetch("SELECT id::text, canonical_name, home_shard_id FROM goals WHERE canonical_name LIKE $1 AND status <> 'merged'", f"{T} %")
    assert len(goals) == 3                                    # callers (paraphrase merged), python-narrower, deploy
    merged = await pool.fetch("SELECT canonical_name, merged_into_id::text AS into FROM goals WHERE canonical_name LIKE $1 AND status = 'merged'", f"{T} %")
    assert all(m["into"] for m in merged) and len(merged) <= 1   # a race-created paraphrase is merged (audit row kept), never deleted
    by_name = {g["canonical_name"]: g["id"] for g in goals}
    procs = await pool.fetch("SELECT name, achieves_goal_id::text AS gid, home_shard_id FROM procedures WHERE name LIKE $1 AND t_invalid IS NULL", f"{T} %")
    assert len(procs) == 5                                    # redelivery created nothing
    survivors = [n for n in (GOAL, GOAL_PARA) if n in by_name]
    assert len(survivors) == 1                                # exactly ONE canonical goal for the two phrasings
    caller_name, caller_goal = survivors[0], by_name[survivors[0]]
    assert {p["name"] for p in procs if p["gid"] == caller_goal} == {f"{T} grep callers", f"{T} lsp find references", f"{T} bazel query rdeps"}
    assert {p["home_shard_id"] for p in procs} == {"K000"} == {g["home_shard_id"] for g in goals}   # sharded: co-located with the goal
    claims = await pool.fetch("SELECT name, properties FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{T} %")
    assert len(claims) == 4 and all(c["properties"].get("source_ref") for c in claims)     # provenance kept
    assert await pool.fetchval("SELECT count(*) FROM ingestion_contexts WHERE source_uri LIKE $1", f"https://example.test/{T}/%") == 5
    rel = await pool.fetchrow("SELECT * FROM goal_relations WHERE specific_goal_id=$1::uuid", by_name[GOAL_NARROW])
    assert str(rel["abstract_goal_id"]) == caller_goal and rel["status"] == "proposed"      # hierarchy stored separately, optional
    dec = await pool.fetch("SELECT decision FROM identity_decisions WHERE object_type='goal' AND candidate_text LIKE $1", f"{T} %")
    assert "same" in {d["decision"] for d in dec}                                           # the dedup decision is auditable

    # 4. projections were updated by the workers and agree with canonical rows
    assert (await sp.verify_projection(pool))["ok"]
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE canonical_name LIKE $1", f"{T} %") == 3   # merged goals leave the index
    assert await pool.fetchval("SELECT count(*) FROM procedure_search_index WHERE name LIKE $1", f"{T} %") == 5

    # 5. real execution evidence (production writer): bazel is the best-proven method
    outcomes = {f"{T} bazel query rdeps": [True] * 9 + [False], f"{T} grep callers": [True, False] * 5,
                f"{T} lsp find references": [True]}          # interleaved: no consecutive-failure quarantine
    for name, results_ in outcomes.items():
        rid = await pool.fetchval("SELECT id::text FROM procedures WHERE name=$1", name)
        for i, ok in enumerate(results_):
            await record_execution_outcome(pool, procedure_row_id=rid, success=ok, context_key=f"ctx-{i % 3}")
    await sp.drain_outbox(pool)

    # 6. query + local claims through the REST-adapter -> canonical retrieval service
    scope = AccessScope.unrestricted()
    res = await ds.find_best_way(pool, "discover call site usages of a function", scope=scope, embedder=EMB,
                                 constraints={"allow_unverified": True})
    assert [g["name"] for g in res["goal_resolution"]["goals"]] == [caller_name]                  # Tier 1: paraphrase resolved to the one goal
    assert res["retrieval"]["mode"] == "jev" and not res["retrieval"]["degraded"]
    assert {p["name"] for p in res["procedures"]} == {f"{T} grep callers", f"{T} lsp find references", f"{T} bazel query rdeps"}  # Tier 2 constrained
    assert res["recommendation"]["name"] == f"{T} bazel query rdeps"                       # evidence-based selection
    assert res["retrieval"]["shards_touched"] == ["K000"]

    res2 = await ds.find_best_way(pool, "discover call site usages of a function", scope=scope, embedder=EMB,
                                  constraints={"allow_unverified": True,
                                               "local_claims": [{"id": "lc-1", "statement": "this repo builds with gradle"}]})
    assert res2["recommendation"]["name"] == f"{T} grep callers"                           # local claim ruled bazel out
    assert res2["query_context"]["local_claim_ids"] == ["lc-1"]
    logged = await pool.fetchrow("SELECT local_claim_ids, mode, selected_procedure_id::text AS sel FROM retrieval_decisions ORDER BY created_at DESC LIMIT 1")
    assert logged["local_claim_ids"] == ["lc-1"] and logged["sel"] == res2["recommendation"]["procedure_id"]

    # 7. hydrated canonical row feeds the EXISTING plan compiler + durable plan store
    row = await pool.fetchrow("SELECT id, procedure_id, version, steps, scope_type, scope_entity_id FROM procedures WHERE id=$1::uuid",
                              res2["recommendation"]["id"])
    compiled = compile_plan(
        procedure_id=row["procedure_id"], procedure_version=row["version"], procedure_row_id=row["id"],
        procedure_payload={"steps": row["steps"], "name": res2["recommendation"]["name"]},
        task_description=f"{T} find callers", scope_type=row["scope_type"], scope_entity_id=row["scope_entity_id"],
        extractor_version="e2e_plan_compiler@1",
        nodes=[{"order": 0, "goal": "run the selected method"}])
    persisted, new = await persist_compiled_plan(pool, compiled)
    assert new and str(persisted.plan.procedure_row_id) == str(row["id"])
    assert await pool.fetchval("SELECT count(*) FROM execution_plans WHERE procedure_row_id=$1", row["id"]) == 1
