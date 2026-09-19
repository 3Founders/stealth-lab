"""Golden retrieval suite (live DB, frozen model fixtures).

The corpus is ingested through the REAL write path (capture_procedure ->
find_or_create_goal -> trigger -> outbox -> drain), then queried through the
production retrieval service. No hand-seeded "final result" rows.

Frozen fixtures stand in for JEV/NLI (recorded verdicts as pure functions);
the optional live model test lives in test_retrieval_live_models.py.
"""
import json
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import search_projection as sp
from app.services.access import AccessScope
from app.services.goals import find_or_create_goal
from app.services.procedures import capture_procedure
from app.services.retrieval_service import RetrievalConfig, find_best_way
from app.services.semantic.errors import ErrorKind, ProviderError
from app.services.shards import ShardPools, register_shard
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, FrozenProvider, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
P = "rg"
SCOPE = AccessScope.unrestricted()
EMB = ConceptEmbedder()


def n(s):  # prefixed, cleaned up by the fixture
    return f"{P} {s}"


G_CALLERS = "find callers of a function"
G_CALLERS_PARA = "locate every call site of a function"
G_DELETE = "find callers and delete them"
G_DEPLOY = "deploy the service safely"
G_MIG_PG = "migrate the database schema on postgres"
G_MIG_MY = "migrate the database schema on mysql"


def ingest_judge():
    return make_judge(FrozenProvider({(n(G_CALLERS_PARA), n(G_CALLERS)): ("same", 0.95)}))


def retrieval_fn(kind, a, b):
    """Frozen NLI/JEV verdicts for the corpus (a = task + local claims, b = candidate)."""
    q = a.lower().split("\nknown in this environment")[0].strip()
    ctx = a.lower()
    bl = b.lower()
    if kind == "task_goal":
        if G_MIG_PG in bl or G_MIG_MY in bl:
            engine = "postgres" if "postgres" in ctx else "mysql" if "mysql" in ctx else None
            if "migrat" not in q:
                return ("unrelated", 0.9)
            if engine is None:
                return ("partial", 0.7)
            return ("matches", 0.9) if engine in bl else ("unrelated", 0.9)
        if G_DELETE in bl:
            return ("matches", 0.9) if "delete" in q else ("unrelated", 0.9)
        if G_CALLERS in bl:
            return ("matches", 0.92) if ("caller" in q or "usages" in q or "call site" in q) and "delete" not in q else ("unrelated", 0.9)
        if G_DEPLOY in bl:
            return ("matches", 0.9) if "deploy" in q else ("unrelated", 0.9)
        return ("unrelated", 0.9)
    if kind == "task_procedure":
        if "bazel" in bl and "gradle" in ctx:
            return ("not_applicable", 0.9)
        return ("applies", 0.9)
    raise AssertionError(kind)


def jev(fail=None):
    return CallbackProvider(retrieval_fn, name="jev", fail=fail)


def nli(fail=None):
    return CallbackProvider(retrieval_fn, name="gemma", fail=fail)


def down(_kind):
    return ProviderError(ErrorKind.TRANSIENT, "503")


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM retrieval_decisions WHERE query_sha256 IS NOT NULL AND created_at > now() - interval '1 hour'")
    await p.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{P} %")
    await p.execute("DELETE FROM procedure_claim_refs WHERE procedure_id IN (SELECT procedure_id FROM procedures WHERE name LIKE $1)", f"{P} %")
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{P} %")
    await p.execute("DELETE FROM goal_relations WHERE specific_goal_id IN (SELECT id FROM goals WHERE canonical_name LIKE $1)", f"{P} %")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{P} %")
    await p.execute("DELETE FROM goal_search_index WHERE canonical_name LIKE $1", f"{P} %")
    await p.execute("DELETE FROM procedure_search_index WHERE name LIKE $1", f"{P} %")
    await p.execute("DELETE FROM object_routes WHERE object_type='procedure' AND object_id NOT IN (SELECT procedure_id FROM procedures)")
    await p.close()


async def add_proc(pool, name, goal, *, attempts=0, successes=0, precond=None, judge=None):  # precond unused (claims drive applicability)
    r = await capture_procedure(
        pool, name=n(name), goal=n(goal), steps=[{"description": name}], provenance="prior_library",
        scope_type="global", goal_embedder=EMB, goal_judge=judge or ingest_judge())
    stats = ({"attempts": attempts, "successes": successes, "distinct_contexts": 1, "mean_steps": None,
                        "times_reused": 0, "context_keys_seen": [], "match_cost_total": 0, "realised_savings_total": 0,
                        "consecutive_failures": 0, "quarantine_entered_at": None,
                        "consecutive_successes_since_quarantine": 0})   # pool has the jsonb codec
    await pool.execute("UPDATE procedures SET verification_stats=$2::jsonb, display_description=$3, embedding=$4::vector, "
                       "embedding_model_id=$5 WHERE id=$1::uuid", r["id"], stats, f"{name} {precond or ''}".strip(),
                       "[" + ",".join(map(str, (await EMB.embed_one(f"{name} {goal}")))) + "]", EMB.embedding_model_id())
    return r


async def build(pool):
    """Ingest the golden corpus through the production path and project it."""
    for g in (G_CALLERS, G_CALLERS_PARA, G_DELETE, G_DEPLOY, G_MIG_PG, G_MIG_MY):
        await find_or_create_goal(pool, canonical_name=n(g), scope_type="global", provenance="prior_library",
                                  embedder=EMB, judge=ingest_judge(), status="active")
    await sp.drain_outbox(pool)
    # embeddings on goals come from find_or_create_goal; procedures get theirs in add_proc


async def query(pool, text, *, judge=None, claims=(), embedder=EMB, pools=None, **kw):
    await sp.drain_outbox(pool)
    return await find_best_way(pool, text, local_claims=claims, scope=SCOPE, embedder=embedder,
                               judge=judge or make_judge(jev()), pools=pools, **kw)


def goal_names(res):
    return [g["name"] for g in res["goal_resolution"]["goals"]]


# ---------------------------------------------------------------- Tier 1


@pytest.mark.asyncio
async def test_dedup_at_ingestion_collapsed_the_paraphrase(pool):
    await build(pool)
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1 AND canonical_name LIKE '%call%'", f"{P} %") == 2  # callers + delete-them


@pytest.mark.asyncio
async def test_goal_exact_lexical_match(pool):
    await build(pool)
    res = await query(pool, G_CALLERS)
    assert goal_names(res) == [n(G_CALLERS)]
    assert res["goal_resolution"]["status"] == "matches" and res["retrieval"]["mode"] == "jev"


@pytest.mark.asyncio
async def test_goal_vector_only_semantic_match(pool):
    await build(pool)
    res = await query(pool, "discover usages")          # shares NO word with the goal text
    cand = {c["name"]: c for c in res["goal_resolution"]["candidates"]}[n(G_CALLERS)]
    assert cand.get("fts_rank") is None and cand.get("vec_rank") is not None   # found by ANN only
    assert n(G_CALLERS) in goal_names(res)


@pytest.mark.asyncio
async def test_misleading_lexical_overlap_and_two_similar_distinct_goals(pool):
    await build(pool)
    res = await query(pool, G_DELETE)
    assert goal_names(res) == [n(G_DELETE)]              # not the (lexically near) callers goal
    res2 = await query(pool, G_CALLERS)
    assert n(G_DELETE) not in goal_names(res2)


@pytest.mark.asyncio
async def test_local_claims_change_the_resolved_goal(pool):
    await build(pool)
    none = await query(pool, "migrate the database schema")
    assert none["goal_resolution"]["status"] == "partial" and len(goal_names(none)) == 2      # ambiguous without context
    pg = await query(pool, "migrate the database schema", claims=[{"id": "c-pg", "statement": "the project database is postgres"}])
    my = await query(pool, "migrate the database schema", claims=[{"id": "c-my", "statement": "the project database is mysql"}])
    assert goal_names(pg) == [n(G_MIG_PG)] and goal_names(my) == [n(G_MIG_MY)]
    assert pg["query_context"]["local_claim_ids"] == ["c-pg"]
    row = await pool.fetchrow("SELECT local_claim_ids, mode FROM retrieval_decisions ORDER BY created_at DESC LIMIT 1")
    assert row["local_claim_ids"] == ["c-my"] and row["mode"] == "jev"                       # auditable


@pytest.mark.asyncio
async def test_only_a_bounded_working_set_of_local_claims_is_used(pool):
    await build(pool)
    many = [{"id": f"c{i}", "statement": f"unrelated fact number {i}"} for i in range(300)]
    many.append({"id": "c-rel", "statement": "the project database is postgres"})
    res = await query(pool, "migrate the database schema", claims=many)
    used = res["query_context"]["local_claim_ids"]
    assert len(used) <= RetrievalConfig().local_claim_budget and "c-rel" in used
    assert res["query_context"]["dropped_local_claims"] >= 280


@pytest.mark.asyncio
async def test_jev_unavailable_falls_back_to_the_nli_model_not_a_heuristic(pool):
    await build(pool)
    j = make_judge(jev(fail=down), nli())
    res = await query(pool, G_CALLERS, judge=j)
    assert res["retrieval"]["mode"] == "model" and res["retrieval"]["providers"] == ["gemma"]
    assert goal_names(res) == [n(G_CALLERS)] and not res["retrieval"]["degraded"]


@pytest.mark.asyncio
async def test_nli_unavailable_uses_jev_alone(pool):
    await build(pool)
    res = await query(pool, G_CALLERS, judge=make_judge(jev(), nli(fail=down)))
    assert res["retrieval"]["mode"] == "jev"


@pytest.mark.asyncio
async def test_all_semantic_rerankers_down_returns_degraded_candidates_not_fake_certainty(pool):
    await build(pool)
    await add_proc(pool, "grep callers", G_CALLERS, attempts=10, successes=9)
    res = await query(pool, G_CALLERS, judge=make_judge(jev(fail=down), nli(fail=down)))
    r = res["retrieval"]
    assert r["mode"] == "candidates_only" and r["degraded"] is True
    assert any("no semantic provider answered" in x for x in r["degraded_reasons"])
    assert res["goal_resolution"]["status"] == "unjudged"
    assert all(g.get("judged") is not True for g in res["goal_resolution"]["goals"])
    assert res["recommendation"] is None and res["confidence"] == "none"     # no winner invented
    assert res["procedures"]                                                   # candidates still returned


@pytest.mark.asyncio
async def test_embedding_outage_is_lexical_only_and_flagged(pool):
    await build(pool)
    res = await query(pool, G_CALLERS, embedder=ConceptEmbedder(fail=True))
    assert res["retrieval"]["degraded"] and any("embedding provider unavailable" in x for x in res["retrieval"]["degraded_reasons"])
    assert goal_names(res) == [n(G_CALLERS)]


@pytest.mark.asyncio
async def test_scope_private_goals_do_not_leak_through_the_projection(pool):
    await build(pool)
    await find_or_create_goal(pool, canonical_name=n("private secret rollout plan"), scope_type="global", provenance="prior_library",
                              embedder=EMB, judge=ingest_judge(), visibility="private", owner_id="alice", status="active")
    await sp.drain_outbox(pool)
    anon = await find_best_way(pool, "private secret rollout plan", scope=AccessScope(viewer_id=None), embedder=EMB, judge=make_judge(jev()))
    assert all("secret rollout" not in c["name"] for c in anon["goal_resolution"]["candidates"])
    alice = await find_best_way(pool, "private secret rollout plan", scope=AccessScope(viewer_id="alice"), embedder=EMB, judge=make_judge(jev()))
    assert any("secret rollout" in c["name"] for c in alice["goal_resolution"]["candidates"])


# ---------------------------------------------------------------- Tier 2


@pytest.mark.asyncio
async def test_same_goal_multiple_procedures_and_other_goal_procedure_never_outranks(pool):
    await build(pool)
    await add_proc(pool, "grep callers", G_CALLERS, attempts=10, successes=9)
    await add_proc(pool, "lsp find references", G_CALLERS_PARA, attempts=3, successes=3)         # same Goal (paraphrase)
    await add_proc(pool, "find callers of a function via deploy", G_DEPLOY, attempts=50, successes=50)  # other goal, lexical bait
    res = await query(pool, G_CALLERS)
    names = {p["name"] for p in res["procedures"]}
    assert names == {n("grep callers"), n("lsp find references")}
    assert len({p["goal_id"] for p in res["procedures"]}) == 1


@pytest.mark.asyncio
async def test_evidence_changes_preference_and_pareto_alternatives_are_kept(pool):
    await build(pool)
    await add_proc(pool, "well proven", G_CALLERS, attempts=40, successes=38)
    await add_proc(pool, "barely tried", G_CALLERS, attempts=2, successes=2)
    res = await query(pool, G_CALLERS)
    assert res["recommendation"]["name"] == n("well proven")
    assert res["recommendation"]["evidence"]["lcb"] > next(p for p in res["procedures"] if p["name"] == n("barely tried"))["evidence"]["lcb"]
    # both are applicable with equal applicability: the lower-evidence one is dominated, so it is an alternative not the winner
    assert [a["name"] for a in res["alternatives"]] == [n("barely tried")]
    # flip the evidence -> preference flips
    await pool.execute("UPDATE procedures SET verification_stats = jsonb_set(jsonb_set(verification_stats,'{attempts}','40'),'{successes}','5') WHERE name=$1", n("well proven"))
    await sp.drain_outbox(pool)
    res2 = await query(pool, G_CALLERS)
    assert res2["recommendation"]["name"] == n("barely tried")


@pytest.mark.asyncio
async def test_no_evidence_means_no_invented_winner(pool):
    await build(pool)
    await add_proc(pool, "untried a", G_CALLERS)
    await add_proc(pool, "untried b", G_CALLERS)
    res = await query(pool, G_CALLERS)
    assert res["recommendation"] is None and len(res["alternatives"]) == 2
    assert "no winner invented" in res["reason"]


@pytest.mark.asyncio
async def test_local_claims_change_procedure_applicability(pool):
    await build(pool)
    await add_proc(pool, "bazel query callers", G_CALLERS, attempts=20, successes=20)
    await add_proc(pool, "grep callers", G_CALLERS, attempts=5, successes=4)
    plain = await query(pool, G_CALLERS)
    assert plain["recommendation"]["name"] == n("bazel query callers")
    gradle = await query(pool, G_CALLERS, claims=[{"id": "c-g", "statement": "this repo builds with gradle"}])
    assert gradle["recommendation"]["name"] == n("grep callers")
    assert any(d["failed"] == ["semantic:not_applicable"] for d in gradle["retrieval"]["disqualified"])


@pytest.mark.asyncio
async def test_procedures_are_never_searched_without_a_resolved_goal(pool):
    await build(pool)
    await add_proc(pool, "orphan lexical bait callers", G_DEPLOY, attempts=9, successes=9)
    res = await query(pool, "orphan lexical bait")
    assert res["goal_resolution"]["status"] in ("none",) and res["procedures"] == [] and res["recommendation"] is None
    assert "never searched globally" in res["reason"]


# ---------------------------------------------------------------- shards


class CountingPool:
    """Wraps a real pool; counts queries so tests can prove batching / no fan-out."""

    def __init__(self, inner):
        self.inner, self.fetch_calls = inner, 0

    async def fetch(self, *a, **k):
        self.fetch_calls += 1
        return await self.inner.fetch(*a, **k)

    async def fetchrow(self, *a, **k):
        return await self.inner.fetchrow(*a, **k)

    async def execute(self, *a, **k):
        return await self.inner.execute(*a, **k)

    async def close(self):
        await self.inner.close()


async def _remote_shard(pool, sid, schema):
    await pool.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    await pool.execute(f"CREATE SCHEMA {schema}")
    await pool.execute(f"CREATE TABLE {schema}.procedures (LIKE public.procedures INCLUDING DEFAULTS)")
    dsn = os.environ["DATABASE_URL"]
    os.environ[f"{sid}_DSN"] = dsn + ("&" if "?" in dsn else "?") + f"options=-csearch_path%3D{schema}"
    await register_shard(pool, sid, dsn_env=f"{sid}_DSN")


async def _move_procedure_to(pool, pools, pid_row, sid):
    """Physically relocate a canonical procedure row to a remote shard (operator-style migration)."""
    row = await pool.fetchrow("SELECT * FROM procedures WHERE id=$1::uuid", pid_row)
    shard = await pools.get(sid)
    # operator-style relocation: copy the columns hydration reads, drop the local row
    await shard.execute(
        "INSERT INTO procedures (id, procedure_id, name, goal, steps, verification_stats, achieves_goal_id, home_shard_id, "
        "display_description, scope_type, is_engineering_fixture) VALUES ($1::uuid,$2::uuid,$3,$4,$5::jsonb,$6::jsonb,$7::uuid,$8,$9,'global',false)",
        str(row["id"]), str(row["procedure_id"]), row["name"], row["goal"], row["steps"], row["verification_stats"],
        str(row["achieves_goal_id"]), sid, row["display_description"])
    await pool.execute("DELETE FROM procedures WHERE id=$1::uuid", pid_row)
    await pool.execute("INSERT INTO object_routes (object_type, object_id, home_shard_id) VALUES ('procedure', $1::uuid, $2) "
                       "ON CONFLICT (object_type, object_id) DO UPDATE SET home_shard_id = EXCLUDED.home_shard_id", str(row["procedure_id"]), sid)
    await pool.execute("INSERT INTO procedure_search_index (procedure_id, procedure_row_id, name, summary, goal_id, search_text, search_tsv, "
                       "home_shard_id, status, version, visibility, updated_at) VALUES ($1::uuid,$2::uuid,$3,$4,$5::uuid,$6,to_tsvector('english',$6),$7,'active',1,'public',now()) "
                       "ON CONFLICT (procedure_id) DO UPDATE SET home_shard_id=EXCLUDED.home_shard_id, procedure_row_id=EXCLUDED.procedure_row_id, "
                       "search_text=EXCLUDED.search_text, search_tsv=EXCLUDED.search_tsv", str(row["procedure_id"]), str(row["id"]),
                       row["name"], row["display_description"], str(row["achieves_goal_id"]), f"{row['name']} {row['goal']}", sid)
    await pool.execute("DELETE FROM projection_outbox WHERE object_id=$1::uuid", str(row["procedure_id"]))


@pytest.mark.asyncio
async def test_candidates_on_multiple_shards_are_hydrated_in_one_batch_per_shard_and_untouched_shards_are_not_queried(pool):
    await build(pool)
    a = await add_proc(pool, "local grep", G_CALLERS, attempts=10, successes=9)
    b = await add_proc(pool, "remote lsp one", G_CALLERS, attempts=10, successes=8)
    c = await add_proc(pool, "remote lsp two", G_CALLERS, attempts=10, successes=7)
    await sp.drain_outbox(pool)
    await _remote_shard(pool, "K901", "k901")
    await _remote_shard(pool, "K903", "k903")            # registered, holds nothing relevant
    pools = ShardPools(pool)
    remote, idle = CountingPool(await pools.get("K901")), CountingPool(await pools.get("K903"))
    pools.inject("K901", remote)
    pools.inject("K903", idle)
    await _move_procedure_to(pool, pools, b["id"], "K901")
    await _move_procedure_to(pool, pools, c["id"], "K901")
    res = await query(pool, G_CALLERS, pools=pools)
    assert {p["name"] for p in res["procedures"]} == {n("local grep"), n("remote lsp one"), n("remote lsp two")}
    assert {p["home_shard_id"] for p in res["procedures"]} == {"K000", "K901"}
    assert remote.fetch_calls == 1 and idle.fetch_calls == 0                 # batched, no fan-out
    assert res["retrieval"]["shards_touched"] == ["K000", "K901"]
    assert not res["retrieval"]["degraded"]
    await pool.execute("DELETE FROM procedure_search_index WHERE name LIKE $1", f"{P} %")
    await pool.execute("DELETE FROM object_routes WHERE home_shard_id IN ('K901','K903')")
    await pools.close()
    for s in ("k901", "k903"):
        await pool.execute(f"DROP SCHEMA {s} CASCADE")
    await pool.execute("DELETE FROM knowledge_shards WHERE shard_id IN ('K901','K903')")


@pytest.mark.asyncio
async def test_unavailable_shard_is_reported_as_partial_not_silently_absent(pool):
    await build(pool)
    a = await add_proc(pool, "local grep", G_CALLERS, attempts=10, successes=9)
    b = await add_proc(pool, "stranded lsp", G_CALLERS, attempts=10, successes=9)
    await sp.drain_outbox(pool)
    os.environ["K902_DSN"] = "postgresql://nobody:x@127.0.0.1:1/none"
    await register_shard(pool, "K902", dsn_env="K902_DSN")
    pools = ShardPools(pool, backoff_s=60)
    # re-home b's route/projection to the dead shard (its canonical row stays; the shard cannot be reached)
    await pool.execute("UPDATE object_routes SET home_shard_id='K902' WHERE object_type='procedure' AND object_id=(SELECT procedure_id FROM procedures WHERE id=$1::uuid)", b["id"])
    await pool.execute("UPDATE procedure_search_index SET home_shard_id='K902' WHERE procedure_row_id=$1::uuid", b["id"])
    res = await query(pool, G_CALLERS, pools=pools)
    r = res["retrieval"]
    assert "K902" in r["unavailable_shards"] and r["degraded"]
    assert any("shard unavailable" in x for x in r["degraded_reasons"])
    assert r["missing_ids"] == []                                    # an outage is not "doesn't exist"
    assert [p["name"] for p in res["procedures"]] == [n("local grep")]
    await pool.execute("UPDATE object_routes SET home_shard_id='K000' WHERE home_shard_id='K902'")
    await pool.execute("UPDATE procedure_search_index SET home_shard_id='K000' WHERE home_shard_id='K902'")
    await pool.execute("DELETE FROM knowledge_shards WHERE shard_id='K902'")
