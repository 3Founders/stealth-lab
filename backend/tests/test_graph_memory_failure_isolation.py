"""
The contamination guards failure_capture.py depends on but does not itself
enforce: graph_memory.py's `_hydrate` and `retrieve` (experiments/
swebench_pro, not a backend/app service, hence the sys.path bootstrap
below -- same pattern test_htn_agent.py already uses to reach it).

These fail SILENTLY if wrong -- a broken join or a missing exclusion
degrades retrieval quality or leaks a gold answer rather than raising --
so they get their own explicit tests rather than trusting the plan's
reasoning about the SQL.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from app.services.failure_capture import CREATED_BY as FAILURE_CREATED_BY  # noqa: E402
from app.services.retrieval import RetrievalResult, RetrievedNode  # noqa: E402

import graph_memory  # noqa: E402


class FakeDB:
    """Only the query shapes _instance_of / _hydrate issue."""

    def __init__(self):
        self.task_nodes: dict[str, dict] = {}
        self.knowledge_nodes: dict[str, dict] = {}

    def add_task_node(self, skill_ref: str) -> str:
        tid = str(uuid4())
        self.task_nodes[tid] = {"skill_ref": skill_ref}
        return tid

    def add_knowledge_node(self, instance_id: str, node_type: str,
                           created_by: str, properties: dict) -> str:
        nid = str(uuid4())
        self.knowledge_nodes[nid] = {
            "node_type": node_type, "created_by": created_by,
            "properties": {"instance_id": instance_id, **properties},
        }
        return nid

    async def fetchrow(self, query: str, *params):
        q = query.strip()
        if q.startswith("SELECT skill_ref AS iid"):
            tid = str(params[0])
            row = self.task_nodes.get(tid)
            if not row:
                return None
            return {"iid": row["skill_ref"], "name": "t", "props": {}, "created_by": None}
        if q.startswith("SELECT properties->>'instance_id' AS iid"):
            nid = str(params[0])
            row = self.knowledge_nodes.get(nid)
            if not row:
                return None
            return {"iid": row["properties"]["instance_id"], "name": "k",
                    "props": row["properties"], "created_by": row["created_by"]}
        if "task_nodes t LEFT JOIN knowledge_nodes k" in q:
            iid = params[0]
            title = next((r["skill_ref"] for r in self.task_nodes.values()
                         if r["skill_ref"] == iid), None)
            if title is None:
                return None
            match = None
            if "k.node_type = 'code_location'" in q:
                match = next((r for r in self.knowledge_nodes.values()
                             if r["properties"].get("instance_id") == iid
                             and r["node_type"] == "code_location"), None)
            else:
                match = next((r for r in self.knowledge_nodes.values()
                             if r["properties"].get("instance_id") == iid), None)
            kprops = match["properties"] if match else None
            return {"title": title, "tprops": {}, "kprops": kprops}
        raise AssertionError(f"FakeDB.fetchrow: unrecognized query\n{q}")


class TestHydrateNodeTypeGuard:
    """The join ambiguity this module's docstring warns about: two live
    knowledge_nodes can now share the same properties.instance_id."""

    def test_code_location_wins_when_a_failure_mode_node_also_exists(self):
        db = FakeDB()
        db.add_task_node("inst_1")
        db.add_knowledge_node("inst_1", "code_location", "swebench_ingest",
                              {"patch": "real fix content", "files": ["a.py"]})
        db.add_knowledge_node("inst_1", "failure_mode", FAILURE_CREATED_BY,
                              {"reason": "could not find it"})
        full = asyncio.run(graph_memory._hydrate(db, "inst_1"))
        assert full is not None
        assert full["kprops"]["patch"] == "real fix content"
        assert "reason" not in full["kprops"]

    def test_still_works_with_only_a_code_location_node(self):
        """Non-regression: the common case (no failure captured) is
        unaffected by the added filter."""
        db = FakeDB()
        db.add_task_node("inst_2")
        db.add_knowledge_node("inst_2", "code_location", "swebench_ingest",
                              {"patch": "the only fix"})
        full = asyncio.run(graph_memory._hydrate(db, "inst_2"))
        assert full["kprops"]["patch"] == "the only fix"

    def test_no_code_location_node_yields_none_kprops_not_a_failure_mode_leak(self):
        """If ONLY a failure_mode node exists (no ingested solution),
        the join must not silently fall back to it."""
        db = FakeDB()
        db.add_task_node("inst_3")
        db.add_knowledge_node("inst_3", "failure_mode", FAILURE_CREATED_BY,
                              {"reason": "only a failure exists here"})
        full = asyncio.run(graph_memory._hydrate(db, "inst_3"))
        assert full is not None
        assert full["kprops"] is None


class TestRetrieveExcludesFailureModesByDefault:
    def test_a_failure_mode_hit_is_dropped_by_default(self, monkeypatch):
        db = FakeDB()
        db.add_task_node("inst_1")
        db.add_knowledge_node("inst_1", "code_location", "swebench_ingest",
                              {"patch": "real fix", "files": []})
        fid = db.add_knowledge_node("inst_1", "failure_mode", FAILURE_CREATED_BY,
                                    {"reason": "did not find it"})

        async def fake_retrieve(self, query, top_k, expand_depth, max_context_nodes):
            return RetrievalResult(
                nodes=[RetrievedNode(id=UUID(fid), table="knowledge_nodes",
                                     name="failure", description=None, score=0.9)],
                entrypoint_ids=[],
            )
        monkeypatch.setattr(
            "app.services.retrieval.HybridRetriever.retrieve", fake_retrieve)

        class FakeEmbedder:
            pass

        hits, diag = asyncio.run(graph_memory.retrieve(db, "some query", FakeEmbedder()))
        assert hits == []  # the ONLY hit was a failure_mode node -- excluded
        assert diag["nodes_returned"] == 1

    def test_include_failure_modes_true_lets_it_through(self, monkeypatch):
        db = FakeDB()
        db.add_task_node("inst_1")
        fid = db.add_knowledge_node("inst_1", "failure_mode", FAILURE_CREATED_BY,
                                    {"reason": "did not find it"})

        async def fake_retrieve(self, query, top_k, expand_depth, max_context_nodes):
            return RetrievalResult(
                nodes=[RetrievedNode(id=UUID(fid), table="knowledge_nodes",
                                     name="failure", description=None, score=0.9)],
                entrypoint_ids=[],
            )
        monkeypatch.setattr(
            "app.services.retrieval.HybridRetriever.retrieve", fake_retrieve)

        class FakeEmbedder:
            pass

        hits, diag = asyncio.run(graph_memory.retrieve(
            db, "some query", FakeEmbedder(), include_failure_modes=True))
        assert len(hits) == 1
        assert hits[0].instance_id == "inst_1"
