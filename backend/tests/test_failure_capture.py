"""
Tests for failure_capture.py: the survivorship-bias fix for
method_library.py, which stores only successes.

FakeDB mirrors the same pool/connection dual-interface style already used
in test_knowledge_conflict_and_supersession.py, scoped to the exact three
queries capture_failure() issues: look up the instance's task_node, insert
the failure_mode knowledge_node, insert the linking edge.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from app.services.failure_capture import CREATED_BY, capture_failure


class FakeDB:
    def __init__(self):
        self.task_nodes: dict[str, dict] = {}
        self.knowledge_nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def add_task_node(self, skill_ref: str, *, invalid: bool = False) -> str:
        tid = str(uuid4())
        self.task_nodes[tid] = {"skill_ref": skill_ref, "t_invalid": "x" if invalid else None}
        return tid

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

    async def fetchval(self, query: str, *params):
        q = query.strip()
        if q.startswith("SELECT id FROM task_nodes WHERE skill_ref"):
            iid = params[0]
            for tid, row in self.task_nodes.items():
                if row["skill_ref"] == iid and row["t_invalid"] is None:
                    return UUID(tid)
            return None
        if q.startswith("INSERT INTO knowledge_nodes"):
            name, properties, created_by = params
            nid = str(uuid4())
            self.knowledge_nodes[nid] = {
                "id": UUID(nid), "node_type": "failure_mode", "name": name,
                "properties": properties, "created_by": created_by, "t_invalid": None,
            }
            return UUID(nid)
        raise AssertionError(f"FakeDB.fetchval: unrecognized query\n{q}")

    async def execute(self, query: str, *params):
        q = query.strip()
        if q.startswith("INSERT INTO edges"):
            task_id, node_id, properties, created_by = params
            self.edges.append({
                "edge_type": "OWNS", "custom_edge_type": "FAILURE_MODE",
                "source_id": task_id, "source_table": "task_nodes",
                "target_id": node_id, "target_table": "knowledge_nodes",
                "properties": properties, "created_by": created_by,
            })
            return "INSERT 0 1"
        raise AssertionError(f"FakeDB.execute: unrecognized query\n{q}")


class TestCaptureFailure:
    def test_writes_one_node_carrying_the_models_own_reason(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        node_id = asyncio.run(capture_failure(
            db, instance_id="instance_x", repo="acme/thing", arm="htn_memory",
            model="deepseek-v3.2", failing_goal="locate the validator",
            reason="could not find the validation logic in _finder.py",
            last_evidence="search returned nothing for 'validate'",
            stop_reason="finished",
        ))
        assert node_id is not None
        assert len(db.knowledge_nodes) == 1
        node = db.knowledge_nodes[node_id]
        assert node["node_type"] == "failure_mode"
        assert node["created_by"] == CREATED_BY
        props = node["properties"]
        assert props["reason"] == "could not find the validation logic in _finder.py"
        assert props["last_evidence"] == "search returned nothing for 'validate'"
        assert props["failing_goal"] == "locate the validator"
        assert props["instance_id"] == "instance_x"  # load-bearing for hold_out
        assert props["repo"] == "acme/thing"
        assert props["arm"] == "htn_memory"
        assert props["model"] == "deepseek-v3.2"
        assert props["stop_reason"] == "finished"

    def test_writes_exactly_one_linking_edge(self):
        db = FakeDB()
        tid = db.add_task_node("instance_x")
        node_id = asyncio.run(capture_failure(
            db, instance_id="instance_x", repo="r", arm="a", model="m",
            failing_goal="g", reason="r",
        ))
        assert len(db.edges) == 1
        edge = db.edges[0]
        assert edge["source_id"] == UUID(tid)
        assert edge["target_id"] == UUID(node_id)
        assert edge["edge_type"] == "OWNS"
        assert edge["custom_edge_type"] == "FAILURE_MODE"
        # ALSO load-bearing for hold_out -- it invalidates edges by
        # properties->>'instance_id' too, not just knowledge_nodes.
        assert edge["properties"]["instance_id"] == "instance_x"

    def test_no_task_node_is_a_silent_no_op_not_an_error(self):
        db = FakeDB()
        # No add_task_node call -- nothing for this instance exists.
        result = asyncio.run(capture_failure(
            db, instance_id="ghost_instance", repo="r", arm="a", model="m",
            failing_goal="g", reason="r",
        ))
        assert result is None
        assert db.knowledge_nodes == {}
        assert db.edges == []

    def test_invalidated_task_node_is_treated_as_absent(self):
        db = FakeDB()
        # A held-out task_node (t_invalid set) must not be linkable --
        # matches hold_out's own "AND t_invalid IS NULL" convention.
        db.add_task_node("instance_x", invalid=True)
        result = asyncio.run(capture_failure(
            db, instance_id="instance_x", repo="r", arm="a", model="m",
            failing_goal="g", reason="r",
        ))
        assert result is None

    def test_long_goal_is_truncated_in_the_display_name_not_dropped(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        long_goal = "x" * 500
        node_id = asyncio.run(capture_failure(
            db, instance_id="instance_x", repo="r", arm="a", model="m",
            failing_goal=long_goal, reason="r",
        ))
        node = db.knowledge_nodes[node_id]
        assert len(node["name"]) <= 200
        # The FULL text still survives in properties -- only the display
        # name is shortened, nothing about the reason is lost.
        assert node["properties"]["failing_goal"] == long_goal
