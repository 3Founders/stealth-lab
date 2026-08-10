"""
End-to-end test of the Experiment-3 extension: knowledge-conflict
detection -> proxy task_node + CONFLICTS_WITH edges -> trigger creation
-> (simulating debate approval) -> UpdateKnowledgeNodeOp applied via
_supersede_knowledge -> confirm only the new policy is live afterward.

Exercises the REAL code (knowledge_conflict.py, knowledge_update.py)
against a fake DB rich enough to answer every query shape these two
modules issue -- same discipline as test_merge_cluster.py and
test_subtask_reuse.py, not a reimplemented simulation.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import numpy as np
import pytest

from app.models.change import ChangeSet, UpdateKnowledgeNodeOp
from app.services.access import AccessScope
from app.services.knowledge_conflict import detect_and_create_conflict_trigger, find_conflicting_knowledge
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater


def unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


def vec_at_similarity(base, target_cosine: float, seed: int, dim: int = 16):
    """
    Deterministically constructs a unit vector at EXACTLY the given
    cosine similarity to `base`, rather than relying on a noise
    magnitude that "usually" lands in a target band -- that was the
    actual bug in the first version of these tests: fixed noise
    scale + random seed landed inside vs. outside the partial-match
    band unpredictably, a test-construction flaw, not an implementation
    bug (confirmed by isolating it before assuming otherwise).
    """
    rng = np.random.default_rng(seed)
    base = unit(base)
    # a random vector, then Gram-Schmidt it orthogonal to base
    r = rng.normal(size=dim)
    r -= np.dot(r, base) * base
    orth = unit(r)
    return unit(target_cosine * base + np.sqrt(max(0, 1 - target_cosine**2)) * orth)


class FakeDB:
    """Implements the pool-level (fetch/fetchrow/execute/acquire) AND
    connection-level interface with the SAME object, since every
    function under test either calls pool.X directly or receives `self`
    as `conn` inside an acquire()/transaction() block."""

    def __init__(self):
        self.knowledge_nodes: dict[str, dict] = {}
        self.task_nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.triggers: list[dict] = []
        self.debates: list[dict] = []

    def add_knowledge_node(self, name: str, embedding, properties=None) -> str:
        nid = str(uuid4())
        self.knowledge_nodes[nid] = {
            "id": UUID(nid), "tenant_id": UUID("00000000-0000-0000-0000-000000000001"),
            "name": name, "node_type": "policy",
            "properties": properties or {}, "embedding": unit(embedding), "t_invalid": None,
        }
        return nid

    def acquire(self):
        @asynccontextmanager
        async def _acquire():
            yield self
        return _acquire()

    def transaction(self):
        @asynccontextmanager
        async def _txn():
            yield
        return _txn()

    # --- fetch (multi-row) ---
    async def fetch(self, query: str, *params):
        q = query.strip()

        if "FROM knowledge_nodes a JOIN knowledge_nodes b" in q:
            new_id = str(params[-1])
            a = self.knowledge_nodes[new_id]
            rows = []
            for bid, b in self.knowledge_nodes.items():
                if bid == new_id or b["t_invalid"] is not None:
                    continue
                sim = float(np.dot(a["embedding"], b["embedding"]))
                rows.append({"id": UUID(bid), "name": b["name"], "similarity": sim})
            rows.sort(key=lambda r: -r["similarity"])
            return rows

        if q.startswith("SELECT id, edge_type::text"):  # edge rewiring lookup
            node_id = str(params[0])
            return [
                dict(e) for e in self.edges
                if e["t_invalid"] is None and e["edge_type"] != "SUPERSEDES"
                and ((str(e["source_id"]) == node_id and e["source_table"] == "knowledge_nodes")
                     or (str(e["target_id"]) == node_id and e["target_table"] == "knowledge_nodes"))
            ]

        if q.startswith("SELECT id, name, properties->>'content' AS content FROM knowledge_nodes"):
            # Added when create_conflict_trigger_for_pair started fetching
            # real content for compute_overlap() -- this FakeDB case was
            # missing (test suite fell out of sync with that real change,
            # confirmed by re-running against the real query text, not
            # assumed).
            ids = [str(i) for i in params[0]]
            rows = []
            for nid in ids:
                node = self.knowledge_nodes.get(nid)
                if node is None or node["t_invalid"] is not None:
                    continue
                rows.append({
                    "id": node["id"], "name": node["name"],
                    "content": node["properties"].get("content", ""),
                })
            return rows

        raise AssertionError(f"FakeDB.fetch: unrecognized query\n{q}")

    # --- fetchrow (single row) ---
    async def fetchrow(self, query: str, *params):
        q = query.strip()

        if q.startswith("SELECT name FROM knowledge_nodes WHERE id = $1"):
            node = self.knowledge_nodes.get(str(params[0]))
            return {"name": node["name"]} if node else None

        if q.startswith("SELECT * FROM knowledge_nodes WHERE id"):
            node = self.knowledge_nodes.get(str(params[0]))
            if node is None or node["t_invalid"] is not None:
                return None
            return dict(node)

        if q.startswith("INSERT INTO task_nodes"):
            nid = str(uuid4())
            self.task_nodes[nid] = {"id": UUID(nid), "name": params[0], "t_invalid": None}
            return {"id": UUID(nid)}

        if q.startswith("INSERT INTO knowledge_nodes"):
            # cols dynamic per _KNOWLEDGE_CARRY_FORWARD; last 2 params are approver/now-ish,
            # values correspond to cols in order -- reconstruct generically
            nid = str(uuid4())
            cols = ("tenant_id", "node_type", "name", "properties", "embedding")
            values = params[: len(cols)]
            row = dict(zip(cols, values))
            self.knowledge_nodes[nid] = {
                "id": UUID(nid), "name": row["name"], "node_type": row["node_type"],
                "properties": row["properties"], "embedding": row["embedding"], "t_invalid": None,
            }
            return {"id": UUID(nid)}

        if "SELECT 1 FROM triggers t" in q:
            task_node_id = str(params[0])
            unresolved = any(
                t["task_node_id"] == task_node_id and t.get("resolved") is not True
                for t in self.triggers
            )
            return {"?": 1} if unresolved else None

        if q.startswith("INSERT INTO triggers"):
            tid = str(uuid4())
            self.triggers.append({
                "id": tid, "task_node_id": str(params[0]), "rule_name": params[1],
                "metric_name": params[2], "resolved": False,
            })
            return {"id": UUID(tid)}

        raise AssertionError(f"FakeDB.fetchrow: unrecognized query\n{q}")

    # --- execute (no return) ---
    async def execute(self, query: str, *params):
        q = query.strip()

        if q.startswith("INSERT INTO edges") and "'VALIDATED_BY', 'CONFLICTS_WITH'" in q:
            proxy_id, target_id, props, now, approver = params
            self.edges.append({
                "id": uuid4(), "edge_type": "VALIDATED_BY", "custom_edge_type": "CONFLICTS_WITH",
                "source_id": proxy_id, "source_table": "task_nodes",
                "target_id": target_id, "target_table": "knowledge_nodes",
                "properties": props, "t_invalid": None,
            })
            return

        if q.startswith("INSERT INTO edges") and "'SUPERSEDES'" in q:
            new_id, old_id, props, now, approver = params
            self.edges.append({
                "id": uuid4(), "edge_type": "SUPERSEDES", "custom_edge_type": None,
                "source_id": new_id, "source_table": "knowledge_nodes",
                "target_id": old_id, "target_table": "knowledge_nodes",
                "properties": props, "t_invalid": None,
            })
            return

        if q.startswith("INSERT INTO edges"):  # generic rewired-edge insert
            (edge_type, custom_edge_type, source_id, source_table,
             target_id, target_table, properties, now, approver) = params
            self.edges.append({
                "id": uuid4(), "edge_type": edge_type, "custom_edge_type": custom_edge_type,
                "source_id": source_id, "source_table": source_table,
                "target_id": target_id, "target_table": target_table,
                "properties": properties, "t_invalid": None,
            })
            return

        if q.startswith("UPDATE knowledge_nodes SET t_invalid"):
            node_id, now = str(params[0]), params[1]
            self.knowledge_nodes[node_id]["t_invalid"] = now
            return

        if q.startswith("UPDATE edges SET t_invalid"):
            edge_id, now = params[0], params[1]
            for e in self.edges:
                if e["id"] == edge_id:
                    e["t_invalid"] = now
            return

        raise AssertionError(f"FakeDB.execute: unrecognized query\n{q}")

    def live_knowledge_nodes(self):
        return {nid: n for nid, n in self.knowledge_nodes.items() if n["t_invalid"] is None}

    def live_edges(self):
        return [e for e in self.edges if e["t_invalid"] is None]


# ---------------------------------------------------------------------

def test_find_conflicting_knowledge_detects_the_partial_match_band():
    db = FakeDB()
    base = unit(np.ones(16))
    old_id = db.add_knowledge_node("Refunds require a receipt", base)
    # A genuinely conflicting update: related (same topic) but not a
    # near-duplicate -- lands in the partial-match band by construction.
    new_id = db.add_knowledge_node(
        "Refunds under $50 do not require a receipt",
        vec_at_similarity(base, 0.80, seed=1),
    )

    conflict = asyncio.run(find_conflicting_knowledge(db, new_id, scope=AccessScope.unrestricted()))
    assert conflict is not None
    assert conflict["id"] == old_id


def test_no_conflict_when_nothing_is_in_the_partial_match_band():
    db = FakeDB()
    db.add_knowledge_node("Refunds require a receipt", unit(np.ones(16)))
    unrelated_id = db.add_knowledge_node("How to configure the CI pipeline", unit(-np.ones(16)))

    conflict = asyncio.run(find_conflicting_knowledge(db, unrelated_id, scope=AccessScope.unrestricted()))
    assert conflict is None


def test_full_pipeline_detect_trigger_supersede_and_confirm_live_state():
    db = FakeDB()
    base = unit(np.ones(16))
    old_id = db.add_knowledge_node("Refunds require a receipt", base)
    new_id = db.add_knowledge_node(
        "Refunds under $50 do not require a receipt",
        vec_at_similarity(base, 0.80, seed=2),
    )

    # Step 1: detect the conflict, create proxy task + edges + trigger
    trigger_id = asyncio.run(detect_and_create_conflict_trigger(
        db, new_id, scope=AccessScope.unrestricted(),
    ))
    assert trigger_id is not None
    assert len(db.task_nodes) == 1, "exactly one proxy reconciliation task should be created"
    conflicts_with_edges = [e for e in db.live_edges() if e["custom_edge_type"] == "CONFLICTS_WITH"]
    assert len(conflicts_with_edges) == 2, "proxy must link to BOTH conflicting knowledge nodes"
    linked_targets = {str(e["target_id"]) for e in conflicts_with_edges}
    assert linked_targets == {old_id, new_id}

    # Step 2: simulate what a debate would decide (real panel run needs
    # live API keys, out of scope for this test) -- the NEW node's
    # content is correct, so it supersedes the OLD one.
    change_set = ChangeSet(ops=[UpdateKnowledgeNodeOp(
        knowledge_node_id=UUID(old_id),
        changes={"name": "Refunds under $50 do not require a receipt"},
        reason="policy updated effective T1, cited both conflicting nodes",
    )])

    updater = KnowledgeUpdater(db)
    result = asyncio.run(updater.apply(change_set, approver_id="test_panel", at=datetime.now(timezone.utc)))
    assert len(result) == 1
    assert result[0]["op"] == "update_knowledge_node"

    # Step 3: the actual claim under test -- does a fresh read serve
    # ONLY the corrected policy afterward?
    live = db.live_knowledge_nodes()
    assert old_id not in live, "the old policy must be invalidated, not still live"
    superseded_id = result[0]["new_id"]
    assert superseded_id in live
    assert live[superseded_id]["name"] == "Refunds under $50 do not require a receipt"

    # And the proxy reconciliation task's CONFLICTS_WITH edge to the old
    # node must have followed forward to the new superseding node --
    # otherwise the audit trail dead-ends at an invalidated row.
    live_conflicts = [e for e in db.live_edges() if e["custom_edge_type"] == "CONFLICTS_WITH"]
    live_targets = {str(e["target_id"]) for e in live_conflicts}
    assert old_id not in live_targets
    assert superseded_id in live_targets


def test_superseding_an_already_superseded_node_fails_cleanly():
    db = FakeDB()
    old_id = db.add_knowledge_node("Refunds require a receipt", unit(np.ones(16)))
    db.knowledge_nodes[old_id]["t_invalid"] = datetime.now(timezone.utc)  # already gone

    change_set = ChangeSet(ops=[UpdateKnowledgeNodeOp(
        knowledge_node_id=UUID(old_id), changes={"name": "x"}, reason="stale approval",
    )])
    updater = KnowledgeUpdater(db)
    with pytest.raises(ChangeApplicationError):
        asyncio.run(updater.apply(change_set, approver_id="test", at=datetime.now(timezone.utc)))
