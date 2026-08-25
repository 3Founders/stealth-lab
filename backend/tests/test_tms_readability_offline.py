"""
DB-free regression tests for Band 2.7 TMS readability: a claim whose
truth_state has been flipped to 'OUT' by relate_claims()'s
SUPERSEDES/CONTRADICTS step must vanish from EVERY retrieval path, while
the bi-temporal columns stay untouched so its history remains queryable.

Two offline proof layers here:

  1. QUERY CONTENT -- a recording pool captures every SQL string
     retrieval.py / local_retrieval.py issue; each knowledge_nodes leg
     must carry NOT_TRUTH_STATE_OUT, each task_nodes leg must NOT
     (task_nodes has no properties column -- splicing the predicate into
     that leg would be a SQL error, not a no-op). Same discipline as
     test_applicability_cascade_offline.py: call-count/query-content
     facts the live e2e suite cannot isolate against real Postgres.
     Expansion coverage matters most offline: the SUPERSEDES edge links
     an OUT claim directly to its live successor, so graph expansion was
     the path guaranteed to reintroduce what search just excluded.

  2. WRITE-SIDE PRESERVATION -- relate_claims() must reach 'OUT' by
     MERGING one JSONB key, never by invalidating or deleting the row:
     that is exactly the property making "vanishes from retrieval" and
     "stays queryable as history" simultaneously true.

The live-database halves of these proofs are in
test_tms_readability_e2e.py (skips without DATABASE_URL).
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from app.db.graph_store import GraphEdge
from app.services.claims import relate_claims
from app.services.local_retrieval import _tier_candidates_by_path
from app.services.retrieval import NOT_TRUTH_STATE_OUT, HybridRetriever


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.1] * 1024


class RecordingPool:
    """
    Records the normalized SQL of every fetch/fetchrow/execute call and
    returns benign empties. Enough to drive retrieve() through all of
    its query-building stages when fed rows (below) -- the point is WHAT
    SQL gets built, not what it returns.
    """

    def __init__(self):
        self.queries: list[str] = []

    def _record(self, sql: str) -> str:
        normalized = " ".join(sql.split())
        self.queries.append(normalized)
        return normalized

    async def fetch(self, sql, *params):
        self._record(sql)
        return []

    async def fetchrow(self, sql, *params):
        self._record(sql)
        return None

    async def execute(self, sql, *params):
        self._record(sql)
        return "UPDATE 0"


def _retriever_with_expansion(edges: list[GraphEdge]) -> tuple[HybridRetriever, RecordingPool]:
    """A retriever whose search stages return one knowledge_nodes hit and
    whose traversal returns `edges` -- so hydration AND expansion both
    build their queries instead of short-circuiting on empty results."""
    pool = RecordingPool()
    retriever = HybridRetriever(pool, embedder=FakeEmbedder())
    hit_id = uuid4()

    async def fetch_with_search_hits(sql, *params):
        pool._record(sql)
        if "UNION ALL" in sql:
            return [{"id": hit_id, "tbl": "knowledge_nodes"}]
        return []

    async def fetchrow_hydrated(sql, *params):
        pool._record(sql)
        return {"id": hit_id, "name": "tms-offline-hit", "description": None}

    pool.fetch = fetch_with_search_hits
    pool.fetchrow = fetchrow_hydrated

    class StubGraph:
        async def traverse_from(self, ids, table, max_depth=2, edge_types=None, as_of=None):
            return edges

    retriever._graph = StubGraph()
    return retriever, pool


# --- layer 1: the predicate reaches every retrieval stage ---


def test_the_predicate_is_the_null_safe_form_not_bare_inequality():
    """Pinned byte-for-byte because the whole design rides on this
    operator: IS DISTINCT FROM keeps every row where the truth_state KEY
    IS ABSENT (all non-claim nodes, all pre-claims.py rows). A bare
    `<> 'OUT'` evaluates to NULL for those rows and would hide them,
    blanking the entire graph out of retrieval."""
    assert NOT_TRUTH_STATE_OUT == "(properties->>'truth_state' IS DISTINCT FROM 'OUT')"


def test_vector_search_filters_out_claims_on_the_knowledge_leg_only():
    pool = RecordingPool()
    retriever = HybridRetriever(pool, embedder=FakeEmbedder())
    asyncio.run(retriever._vector_search([0.1] * 1024, limit=5))
    assert len(pool.queries) == 1
    sql = pool.queries[0]
    # Exactly one predicate occurrence: the knowledge_nodes leg. The
    # task_nodes leg must be clean -- it has no properties column.
    assert sql.count(NOT_TRUTH_STATE_OUT) == 1
    kn_leg = sql.split("FROM knowledge_nodes")[1]
    assert NOT_TRUTH_STATE_OUT in kn_leg
    task_leg = sql.split("FROM task_nodes")[1].split("UNION ALL")[0]
    assert NOT_TRUTH_STATE_OUT not in task_leg


def test_lexical_search_filters_out_claims_on_the_knowledge_leg_only():
    pool = RecordingPool()
    retriever = HybridRetriever(pool, embedder=FakeEmbedder())
    asyncio.run(retriever._lexical_search("deploy step", limit=5))
    assert len(pool.queries) == 1
    sql = pool.queries[0]
    assert sql.count(NOT_TRUTH_STATE_OUT) == 1
    kn_leg = sql.split("FROM knowledge_nodes")[1]
    assert NOT_TRUTH_STATE_OUT in kn_leg
    tn_leg = sql.split("FROM task_nodes")[1].split("UNION ALL")[0]
    assert NOT_TRUTH_STATE_OUT not in tn_leg


def test_entrypoint_hydration_and_graph_expansion_both_filter_out_claims():
    """The leak the direct-search filters alone cannot close: the
    SUPERSEDES edge recorded by relate_claims() points from the live
    successor straight at the OUT claim, so expansion re-hydration was
    guaranteed to reintroduce exactly what search excluded."""
    old_claim_id = uuid4()
    supersede_edge = GraphEdge(
        id=uuid4(), edge_type="SUPERSEDES", custom_edge_type="SUPERSEDES",
        source_id=uuid4(), source_table="knowledge_nodes",
        target_id=old_claim_id, target_table="knowledge_nodes",
        properties={},
    )
    retriever, pool = _retriever_with_expansion([supersede_edge])
    result = asyncio.run(
        retriever.retrieve("anything", top_k=5, expand_depth=1)
    )
    assert result.entrypoint_ids  # the stub made one entrypoint real
    knowledge_reads = [q for q in pool.queries if "FROM knowledge_nodes WHERE id = $1" in q]
    # One hydration read + one expansion re-hydration read for the OUT claim.
    assert len(knowledge_reads) >= 2
    assert all(NOT_TRUTH_STATE_OUT in q for q in knowledge_reads), (
        "every knowledge_nodes point-read in retrieval -- entrypoint "
        "hydration AND expansion -- must exclude truth_state='OUT'"
    )


def test_task_only_retrieval_never_emits_the_predicate():
    pool = RecordingPool()
    retriever = HybridRetriever(pool, embedder=FakeEmbedder(), tables=("task_nodes",))
    asyncio.run(retriever.retrieve("anything", top_k=5))
    assert pool.queries
    assert all(NOT_TRUTH_STATE_OUT not in q for q in pool.queries)


def test_structural_and_temporal_tier_queries_filter_out_claims():
    """local_retrieval's tiers are independent queries into the same
    tables -- union composition means each tier is its own way back IN
    for a claim the semantic tier already excluded."""
    pool = RecordingPool()

    async def _run():
        structural = await _tier_candidates_by_path(
            pool, ["src/deploy.py"], scope=None, matched_by_label="structural",
        )
        temporal = await _tier_candidates_by_path(
            pool, ["src/deploy.py"], scope=None, matched_by_label="temporal",
        )
        return structural, temporal

    structural, temporal = asyncio.run(_run())
    assert structural == [] and temporal == []  # recording pool returns nothing
    tier_sql = [q for q in pool.queries if "ILIKE ANY" in q]
    assert len(tier_sql) == 4  # two calls x two table legs
    knowledge_legs = [q for q in tier_sql if "FROM knowledge_nodes" in q]
    task_legs = [q for q in tier_sql if "FROM task_nodes" in q]
    assert len(knowledge_legs) == 2 and len(task_legs) == 2
    assert all(NOT_TRUTH_STATE_OUT in q for q in knowledge_legs)
    assert all(NOT_TRUTH_STATE_OUT not in q for q in task_legs)


# --- layer 2: the write side keeps the row queryable ---


class TmsWriteFakeDB:
    """
    Minimal double for relate_claims()'s exact statement shapes (mirrors
    test_claims.py's FakeDB, scoped to this file's question: does the
    flip preserve the row?). Stores claim rows keyed by id and records
    every statement issued.
    """

    def __init__(self):
        self.claims: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.statements: list[str] = []

    def acquire(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _acquire():
            yield self

        return _acquire()

    def transaction(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _txn():
            yield

        return _txn()

    def add_claim(self, props: dict) -> str:
        cid = str(uuid4())
        self.claims[cid] = {"id": cid, "properties": dict(props), "t_invalid": None}
        return cid

    async def execute(self, query: str, *params):
        sql = " ".join(query.split())
        self.statements.append(sql)
        if sql.startswith("INSERT INTO edges") and "SUPERSEDES" in sql:
            relation, from_id, to_id, properties, created_by = params
            self.edges.append({
                "edge_type": "SUPERSEDES", "custom_edge_type": relation,
                "source_id": from_id, "target_id": to_id,
                "properties": properties, "created_by": created_by,
            })
            return "INSERT 0 1"
        if sql.startswith("UPDATE knowledge_nodes SET properties"):
            (claim_id,) = params
            node = self.claims[str(claim_id)]
            node["properties"] = {**node["properties"], "truth_state": "OUT"}
            return "UPDATE 1"
        raise AssertionError(f"TmsWriteFakeDB.execute: unrecognized query\n{sql}")


def test_relate_claims_flips_belief_without_touching_existence_or_history():
    db = TmsWriteFakeDB()
    old_id = db.add_claim({
        "statement": "deploy uses heroku cli", "subject": "deploy",
        "predicate": "uses", "object": "heroku cli", "truth_state": "IN",
    })
    new_id = db.add_claim({"statement": "deploy uses flyctl", "truth_state": "IN"})

    asyncio.run(relate_claims(
        db, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES",
    ))

    # The invalidated row still exists, still t_invalid-free, and every
    # non-TMS property survived the merge -- history intact.
    old_row = db.claims[old_id]
    assert old_row["properties"]["truth_state"] == "OUT"
    assert old_row["properties"]["statement"] == "deploy uses heroku cli"
    assert old_row["properties"]["object"] == "heroku cli"
    assert old_row["t_invalid"] is None

    update_sql = [s for s in db.statements if s.startswith("UPDATE")][0]
    assert "properties || '{\"truth_state\": \"OUT\"}'::jsonb" in update_sql, (
        "the flip must MERGE the JSONB key, not replace properties"
    )
    assert "t_invalid" not in update_sql
    assert not any(s.startswith("DELETE FROM") for s in db.statements)

    # And the justification edge that ties OUT claim to its successor is
    # part of the same transaction -- the history graph stays connected.
    assert db.edges[0]["custom_edge_type"] == "SUPERSEDES"
    assert db.edges[0]["source_id"] == new_id
    assert db.edges[0]["target_id"] == old_id


def test_embedding_wiring_is_untouched_so_out_claims_stay_indexable_as_history():
    """Guard against the cheap wrong fix: 'hide OUT claims' could tempt
    someone to stop embedding them at write time or to null their
    embedding at flip time -- which would make them invisible to
    vector-based HISTORY tooling too. capture_claim's INSERT keeps its
    embedding parameter and retrieval.py never UPDATEs embeddings."""
    db = RecordingPool()
    retriever = HybridRetriever(db, embedder=FakeEmbedder())
    asyncio.run(retriever.retrieve("anything"))
    assert not any("UPDATE" in q or "DELETE" in q for q in db.queries), (
        "retrieval must remain read-only over the nodes it filters"
    )
