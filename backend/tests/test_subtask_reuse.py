"""
Tests Part C (app/services/subtask_reuse.py) against a FAKE database that
the REAL hierarchy.py/subtask_reuse.py code runs against -- not a
reimplemented simulation. This is what actually answers the question
that matters: given a proposed task with some new and some already-
existing subtasks scattered across different branches of the tree, does
resolve_subtask_reuse correctly recognize the existing ones, and how
does its real round-trip / embed-call cost compare to the naive
per-subtask loop it replaces?

No live Postgres in this sandbox, so FakePool pattern-matches on the
fixed set of SQL shapes hierarchy.py issues (there are exactly four:
the roots query, the batched frontier-scoring query, the children
query, and hierarchical_search's single-query scoring) and answers them
from an in-memory graph using real cosine similarity. Every `.fetch()`
call is counted -- that count IS the round-trip number reported below,
not an estimate.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import numpy as np
import pytest

from app.models.change import ChangeSet, CreateEdgeOp, CreateTaskNodeOp
from app.services.access import AccessScope
from app.services.hierarchy import batch_hierarchical_search, hierarchical_search
from app.services.subtask_reuse import resolve_subtask_reuse


def unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


class FakePool:
    """Minimal in-memory stand-in for asyncpg.Pool, answering exactly the
    query shapes hierarchy.py issues. Counts every .fetch() call."""

    def __init__(self):
        self.nodes: dict[str, dict[str, dict]] = {"task_nodes": {}, "knowledge_nodes": {}}
        self.edges: list[dict] = []
        self.fetch_calls = 0

    def add_node(self, table: str, name: str, embedding, postconditions=None) -> str:
        nid = str(uuid4())
        self.nodes[table][nid] = {
            "id": nid, "name": name, "embedding": unit(embedding),
            "props": {"postconditions": postconditions} if postconditions else {},
        }
        return nid

    def add_parent_of(self, table: str, parent_id: str, child_id: str):
        self.edges.append({"source_id": parent_id, "target_id": child_id, "table": table})

    async def fetch(self, query: str, *params):
        self.fetch_calls += 1
        table = "task_nodes" if "task_nodes" in query else "knowledge_nodes"

        if "props" in query and "id = ANY($1::uuid[])" in query and "unnest" not in query and "edges" not in query:
            # batch_hierarchical_search's Rule 1 gate fetch
            ids = [str(i) for i in params[0]]
            return [
                {"id": UUID(nid), "props": self.nodes[table][nid]["props"]}
                for nid in ids if nid in self.nodes[table]
            ]

        if "NOT EXISTS" in query:  # _fetch_roots
            owned = {e["target_id"] for e in self.edges if e["table"] == table}
            return [
                {"id": UUID(nid), "name": n["name"], "has_embedding": True, "full_text": n["name"]}
                for nid, n in self.nodes[table].items() if nid not in owned
            ]

        if "unnest($1::text[], $2::text[])" in query:  # batch frontier scoring
            refs, vec_texts, frontier_ids = params
            frontier_ids = [str(i) for i in frontier_ids]
            rows = []
            for ref, vec_text in zip(refs, vec_texts):
                qvec = unit([float(x) for x in vec_text.strip("[]").split(",")])
                for nid in frontier_ids:
                    n = self.nodes[table][nid]
                    sim = float(np.dot(qvec, n["embedding"]))
                    rows.append({"ref": ref, "id": UUID(nid), "name": n["name"], "similarity": sim})
            return rows

        if "e.source_id = ANY($1::uuid[])" in query:  # children of a frontier
            parent_ids = {str(i) for i in params[0]}
            return [
                {"source_id": UUID(e["source_id"]), "id": UUID(e["target_id"]),
                 "name": self.nodes[table][e["target_id"]]["name"]}
                for e in self.edges if e["table"] == table and e["source_id"] in parent_ids
            ]

        if "1 - (embedding <=> $1::vector)" in query:  # hierarchical_search single-query scoring
            vec_str, ids = params
            qvec = unit([float(x) for x in vec_str.strip("[]").split(",")])
            ids = [str(i) for i in ids]
            return [
                {"id": UUID(nid), "name": self.nodes[table][nid]["name"],
                 "similarity": float(np.dot(qvec, self.nodes[table][nid]["embedding"]))}
                for nid in ids
            ]

        raise AssertionError(f"FakePool.fetch got an unrecognized query shape:\n{query}")


class FakeEmbedder:
    """Returns a pre-registered vector for known text; counts calls
    separately from the pool so embed-call cost is visible on its own."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors
        self.embed_calls = 0
        self.embed_one_calls = 0

    async def embed(self, texts, input_type="document"):
        self.embed_calls += 1
        return [self._vectors[t] for t in texts]

    async def embed_one(self, text, input_type="document"):
        self.embed_one_calls += 1
        return self._vectors[text]


def build_test_graph():
    """
    2 top clusters x 2 mid clusters x 3 leaves = 12 existing task_nodes,
    in a 16-dim space, well-separated so matching is unambiguous. Basis-
    aligned clusters: top cluster 0 lives near dims 0-7, top cluster 1
    near dims 8-15, so cross-branch separation is real, not accidental.
    """
    pool = FakePool()
    rng = np.random.default_rng(3)
    D = 16
    leaf_ids = {}  # name -> id, and store embeddings for later exact-match reuse
    leaf_vecs = {}

    for top in range(2):
        top_center = np.zeros(D); top_center[top * 8: top * 8 + 4] = 1.0
        top_children = []
        for mid in range(2):
            mid_center = unit(top_center + 0.1 * rng.normal(size=D))
            mid_children = []
            for leaf in range(3):
                name = f"existing-t{top}-m{mid}-l{leaf}"
                vec = unit(mid_center + 0.05 * rng.normal(size=D))
                nid = pool.add_node("task_nodes", name, vec)
                leaf_ids[name] = nid
                leaf_vecs[name] = vec
                mid_children.append(nid)
            mid_id = pool.add_node("task_nodes", f"group-t{top}-m{mid}", mid_center)
            for c in mid_children:
                pool.add_parent_of("task_nodes", mid_id, c)
            top_children.append(mid_id)
        top_id = pool.add_node("task_nodes", f"group-t{top}", top_center)
        for c in top_children:
            pool.add_parent_of("task_nodes", top_id, c)

    return pool, leaf_ids, leaf_vecs


# ---------------------------------------------------------------------
# Correctness: does it actually recognize existing subtasks, cross-branch?
# ---------------------------------------------------------------------

def test_recognizes_existing_subtasks_scattered_across_branches():
    pool, leaf_ids, leaf_vecs = build_test_graph()

    # 4 "new" subtasks are near-exact restatements of 4 EXISTING leaves,
    # deliberately pulled from DIFFERENT branches (not all from one
    # cluster) -- this is the actual claim being tested: cross-branch
    # reuse, not just "matches something in the same neighborhood".
    reused_targets = [
        "existing-t0-m0-l0", "existing-t0-m1-l2", "existing-t1-m0-l1", "existing-t1-m1-l0",
    ]
    novel_texts = [f"genuinely novel subtask {i}" for i in range(6)]

    vectors = {}
    ops = []
    for i, target in enumerate(reused_targets):
        text = f"proposed subtask restating {target}"
        vectors[f"{text} "] = unit(leaf_vecs[target] + 0.01 * np.random.default_rng(i).normal(size=16))
        ops.append(CreateTaskNodeOp(ref=f"reused_{i}", name=text, description=""))
    rng2 = np.random.default_rng(99)
    for i, text in enumerate(novel_texts):
        # far from every existing cluster
        vectors[f"{text} "] = unit(np.full(16, -1.0) + 0.05 * rng2.normal(size=16))
        ops.append(CreateTaskNodeOp(ref=f"novel_{i}", name=text, description=""))

    change_set = ChangeSet(ops=ops)
    embedder = FakeEmbedder(vectors)

    new_cs, report = asyncio.run(resolve_subtask_reuse(
        change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder,
    ))

    matched_refs = {m["ref"] for m in report.matches}
    assert matched_refs == {f"reused_{i}" for i in range(4)}, f"expected all 4 reused_* refs matched, got {matched_refs}"

    matched_targets = {m["matched_name"] for m in report.matches}
    assert matched_targets == set(reused_targets), "matched wrong existing nodes"

    remaining_refs = {op.ref for op in new_cs.ops}
    assert remaining_refs == {f"novel_{i}" for i in range(6)}, "novel ops should all survive untouched"

    assert embedder.embed_calls == 1, "must be ONE batched embed call for all 10 proposed ops, not 10"


def test_edges_referencing_a_matched_subtask_are_dropped_not_miswired():
    pool, leaf_ids, leaf_vecs = build_test_graph()
    target = "existing-t0-m0-l1"
    text = f"restating {target}"
    vectors = {f"{text} ": unit(leaf_vecs[target] + 0.01 * np.random.default_rng(1).normal(size=16))}
    ops = [
        CreateTaskNodeOp(ref="a", name=text, description=""),
        CreateEdgeOp(edge_type="REQUIRES", source_ref="a", target_ref="a"),  # self-ref, trivial but must not crash
    ]
    change_set = ChangeSet(ops=ops)
    embedder = FakeEmbedder(vectors)

    new_cs, report = asyncio.run(resolve_subtask_reuse(
        change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder,
    ))
    assert len(report.matches) == 1
    # The edge referenced the now-dropped ref -- must be gone, and the
    # surviving ChangeSet must NEVER contain a real existing-node id
    # (that would violate the generative capability boundary).
    assert new_cs.ops == []
    for op in new_cs.ops:
        assert not hasattr(op, "source_id") or op.source_id is None


def test_no_reuse_when_nothing_matches():
    pool, leaf_ids, leaf_vecs = build_test_graph()
    rng = np.random.default_rng(7)
    vectors = {}
    ops = []
    for i in range(5):
        text = f"totally novel {i}"
        vectors[f"{text} "] = unit(np.full(16, -1.0) + 0.05 * rng.normal(size=16))
        ops.append(CreateTaskNodeOp(ref=f"n{i}", name=text, description=""))
    change_set = ChangeSet(ops=ops)
    embedder = FakeEmbedder(vectors)

    new_cs, report = asyncio.run(resolve_subtask_reuse(
        change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder,
    ))
    assert report.matches == []
    assert len(new_cs.ops) == 5


# ---------------------------------------------------------------------
# Cost: real round-trip / embed-call counts, naive loop vs batched, at
# increasing N -- printed, not just asserted, since these are the
# numbers that matter for the analysis.
# ---------------------------------------------------------------------

async def _naive_per_op_cost(pool: FakePool, embedder: FakeEmbedder, ops):
    """What Part C replaces: one hierarchical_search call per proposed
    op, each of which embeds its own text independently."""
    pool.fetch_calls = 0
    embedder.embed_calls = 0
    embedder.embed_one_calls = 0
    for op in ops:
        await hierarchical_search(
            pool, "task_nodes", f"{op.name} ", scope=AccessScope.unrestricted(),
            embedder=embedder, beam=3, adaptive=True,
        )
    return pool.fetch_calls, embedder.embed_one_calls


async def _batched_cost(pool: FakePool, embedder: FakeEmbedder, change_set):
    pool.fetch_calls = 0
    embedder.embed_calls = 0
    embedder.embed_one_calls = 0
    await resolve_subtask_reuse(change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder)
    return pool.fetch_calls, embedder.embed_calls


def test_precondition_gate_blocks_a_match_end_to_end():
    """
    The exact adversarial case, run through the REAL wired path this
    time (resolve_subtask_reuse -> batch_hierarchical_search -> Rule 1
    gate), not just the standalone gate module. A DE-labeled existing
    node and an incoming SWE-labeled subtask share enough surface text
    to clear FULL_MATCH_THRESHOLD on embedding similarity alone, but
    their stated postconditions conflict -- confirms the gate actually
    blocks it once wired, not just in isolation.
    """
    pool = FakePool()
    shared_direction = unit(np.ones(16))
    de_id = pool.add_node(
        "task_nodes", "validate the output", shared_direction,
        postconditions=["schema_conformance", "field_types_valid"],
    )

    text = "validate the output "
    vectors = {text: unit(shared_direction + 0.01 * np.random.default_rng(1).normal(size=16))}
    ops = [CreateTaskNodeOp(ref="swe_task", name=text.strip(), description="")]
    change_set = ChangeSet(ops=ops)
    embedder = FakeEmbedder(vectors)

    new_cs, report = asyncio.run(resolve_subtask_reuse(
        change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder,
        query_postconditions={"swe_task": ["test_suite_passes", "no_regressions"]},
    ))

    assert report.matches == [], "gate must block this match despite high embedding similarity"
    assert len(new_cs.ops) == 1, "the op must survive as novel, not get wrongly dropped as a duplicate"


def test_precondition_gate_allows_a_genuine_match_end_to_end():
    """Same setup, but incoming postconditions genuinely overlap -- confirms
    the gate isn't just blocking everything, only the mismatched case."""
    pool = FakePool()
    shared_direction = unit(np.ones(16))
    existing_id = pool.add_node(
        "task_nodes", "validate the output", shared_direction,
        postconditions=["schema_conformance", "field_types_valid"],
    )

    text = "validate the output "
    vectors = {text: unit(shared_direction + 0.01 * np.random.default_rng(2).normal(size=16))}
    ops = [CreateTaskNodeOp(ref="de_task", name=text.strip(), description="")]
    change_set = ChangeSet(ops=ops)
    embedder = FakeEmbedder(vectors)

    new_cs, report = asyncio.run(resolve_subtask_reuse(
        change_set, pool, scope=AccessScope.unrestricted(), embedder=embedder,
        query_postconditions={"de_task": ["schema_conformance", "field_types_valid"]},
    ))

    assert len(report.matches) == 1
    assert report.matches[0]["matched_id"] == existing_id
    assert new_cs.ops == []


def test_cost_comparison_naive_vs_batched():
    print("\n\n=== Part C cost: naive per-subtask loop vs batched resolve_subtask_reuse ===")
    for n_subtasks in (4, 8, 20, 50):
        pool, leaf_ids, leaf_vecs = build_test_graph()
        rng = np.random.default_rng(n_subtasks)
        vectors = {}
        ops = []
        targets = list(leaf_vecs.keys())
        for i in range(n_subtasks):
            if i % 3 == 0:  # roughly a third are real reuse hits, scattered
                target = targets[i % len(targets)]
                text = f"subtask {i} restating {target}"
                vectors[f"{text} "] = unit(leaf_vecs[target] + 0.01 * rng.normal(size=16))
            else:
                text = f"subtask {i} genuinely novel"
                vectors[f"{text} "] = unit(np.full(16, -1.0) + 0.05 * rng.normal(size=16))
            ops.append(CreateTaskNodeOp(ref=f"op{i}", name=text, description=""))
        change_set = ChangeSet(ops=ops)
        embedder = FakeEmbedder(vectors)

        naive_fetch, naive_embed = asyncio.run(_naive_per_op_cost(pool, embedder, ops))
        batch_fetch, batch_embed = asyncio.run(_batched_cost(pool, embedder, change_set))

        print(f"  N={n_subtasks:3d}  naive: {naive_fetch:4d} round trips, {naive_embed:3d} embed calls   |  "
              f"batched: {batch_fetch:4d} round trips, {batch_embed:3d} embed calls")

        assert batch_embed == 1
        assert batch_fetch <= naive_fetch
