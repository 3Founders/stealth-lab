"""
Tests for claims.py: claim-level hyper-nodes on the existing schema.

FakeDB mirrors the pool/connection dual-interface style already used in
test_failure_capture.py and test_knowledge_conflict_and_supersession.py,
scoped to the exact queries capture_claim()/relate_claims() issue.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest

from app.services.claims import CREATED_BY, capture_claim as _real_capture_claim, relate_claims


class FakeEmbedder:
    """No real network access needed -- a fixed vector is enough to
    prove capture_claim() actually calls the embedder and passes the
    result through to the INSERT, without touching Voyage."""
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def capture_claim(db, **kwargs):
    """Test-local wrapper: every real capture_claim() call in this
    file goes through here so none of them need to remember to pass
    a fake embedder individually."""
    return await _real_capture_claim(db, embedder=FakeEmbedder(), **kwargs)


class FakeDB:
    def __init__(self):
        self.task_nodes: dict[str, dict] = {}
        self.knowledge_nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.episode_links: list[dict] = []

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

    async def fetch(self, query: str, *params):
        q = query.strip()
        if q.startswith("SELECT id FROM task_nodes WHERE skill_ref = ANY"):
            wanted = set(params[0])
            return [{"id": UUID(tid)} for tid, row in self.task_nodes.items()
                     if row["skill_ref"] in wanted and row["t_invalid"] is None]
        raise AssertionError(f"FakeDB.fetch: unrecognized query\n{q}")

    async def fetchval(self, query: str, *params):
        q = query.strip()
        if q.startswith("INSERT INTO knowledge_nodes"):
            name, properties, embedding, created_by, owner_id, visibility = params
            nid = str(uuid4())
            self.knowledge_nodes[nid] = {
                "id": UUID(nid), "node_type": "claim", "name": name,
                "properties": dict(properties), "embedding": embedding,
                "created_by": created_by, "owner_id": owner_id,
                "visibility": visibility,
            }
            return UUID(nid)
        raise AssertionError(f"FakeDB.fetchval: unrecognized query\n{q}")

    async def execute(self, query: str, *params):
        q = query.strip()
        if q.startswith("INSERT INTO edges") and "PRODUCES" in q:
            node_id, task_id, properties, created_by = params
            self.edges.append({
                "edge_type": "PRODUCES", "custom_edge_type": "CLAIM_OF",
                "source_id": node_id, "source_table": "knowledge_nodes",
                "target_id": task_id, "target_table": "task_nodes",
                "properties": properties, "created_by": created_by,
            })
            return "INSERT 0 1"
        if q.startswith("INSERT INTO edges") and "SUPERSEDES" in q:
            relation, from_id, to_id, properties, created_by = params
            self.edges.append({
                "edge_type": "SUPERSEDES", "custom_edge_type": relation,
                "source_id": UUID(from_id), "source_table": "knowledge_nodes",
                "target_id": UUID(to_id), "target_table": "knowledge_nodes",
                "properties": properties, "created_by": created_by,
            })
            return "INSERT 0 1"
        if q.startswith("INSERT INTO episode_links"):
            episode_id, target_id = params
            self.episode_links.append({
                "episode_id": episode_id, "target_id": target_id,
                "target_table": "knowledge_nodes",
            })
            return "INSERT 0 1"
        if q.startswith("UPDATE knowledge_nodes SET properties"):
            (claim_id,) = params
            node = self.knowledge_nodes[str(claim_id)]
            node["properties"]["truth_state"] = "OUT"
            return "UPDATE 1"
        raise AssertionError(f"FakeDB.execute: unrecognized query\n{q}")


class TestCaptureClaim:
    def test_writes_one_claim_node_carrying_statement_and_truth_state(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        node_id = asyncio.run(capture_claim(
            db, statement="the validator rejects reserved keywords",
            task_ids=["instance_x"],
        ))
        assert node_id is not None
        assert len(db.knowledge_nodes) == 1
        node = db.knowledge_nodes[node_id]
        assert node["node_type"] == "claim"
        assert node["created_by"] == CREATED_BY
        assert node["properties"]["statement"] == "the validator rejects reserved keywords"
        assert node["properties"]["truth_state"] == "IN"

    def test_links_to_every_task_id_given_not_just_the_first(self):
        db = FakeDB()
        db.add_task_node("instance_a")
        db.add_task_node("instance_b")
        node_id = asyncio.run(capture_claim(
            db, statement="shared claim across two tasks",
            task_ids=["instance_a", "instance_b"],
        ))
        assert len(db.edges) == 2
        targets = {e["target_id"] for e in db.edges}
        assert targets == {UUID(tid) for tid in db.task_nodes}
        for e in db.edges:
            assert e["source_id"] == UUID(node_id)
            assert e["edge_type"] == "PRODUCES"
            assert e["custom_edge_type"] == "CLAIM_OF"

    def test_missing_task_ids_are_silently_skipped_not_erroring(self):
        db = FakeDB()
        db.add_task_node("instance_a")
        # instance_ghost does not exist -- only instance_a should get an edge.
        node_id = asyncio.run(capture_claim(
            db, statement="partially resolvable claim",
            task_ids=["instance_a", "instance_ghost"],
        ))
        assert node_id is not None
        assert len(db.edges) == 1

    def test_no_live_task_node_is_a_silent_no_op_not_an_error(self):
        db = FakeDB()
        # No add_task_node call -- nothing for this instance exists.
        result = asyncio.run(capture_claim(
            db, statement="orphaned claim", task_ids=["ghost_instance"],
        ))
        assert result is None
        assert db.knowledge_nodes == {}
        assert db.edges == []

    def test_invalidated_task_node_is_treated_as_absent(self):
        db = FakeDB()
        db.add_task_node("instance_x", invalid=True)
        result = asyncio.run(capture_claim(
            db, statement="claim against a held-out task", task_ids=["instance_x"],
        ))
        assert result is None

    def test_justification_episode_is_linked_when_given(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        episode_id = str(uuid4())
        node_id = asyncio.run(capture_claim(
            db, statement="justified claim", task_ids=["instance_x"],
            justification_episode_id=episode_id,
        ))
        assert len(db.episode_links) == 1
        link = db.episode_links[0]
        assert link["episode_id"] == episode_id
        assert link["target_id"] == UUID(node_id)

    def test_no_justification_given_links_nothing(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        asyncio.run(capture_claim(
            db, statement="unjustified claim", task_ids=["instance_x"],
        ))
        assert db.episode_links == []

    def test_invalid_truth_state_is_rejected(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        with pytest.raises(ValueError):
            asyncio.run(capture_claim(
                db, statement="bad state", task_ids=["instance_x"],
                truth_state="MAYBE",
            ))

    def test_long_statement_is_truncated_in_the_display_name_not_dropped(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        long_statement = "x" * 500
        node_id = asyncio.run(capture_claim(
            db, statement=long_statement, task_ids=["instance_x"],
        ))
        node = db.knowledge_nodes[node_id]
        assert len(node["name"]) <= 200
        # The full text still survives in properties.
        assert node["properties"]["statement"] == long_statement

    def test_embedding_is_actually_set_on_the_row(self):
        """The real bug ticket 03 found and this fix closes: embedding
        was previously omitted from the INSERT entirely, making every
        claim invisible to HybridRetriever (which filters on
        `embedding IS NOT NULL` throughout). Confirms the fix reaches
        the actual row, not just that the function runs. Stored as
        pgvector's real text wire format (to_pgvector), not a raw
        Python list -- asyncpg has no native vector codec."""
        db = FakeDB()
        db.add_task_node("instance_x")
        node_id = asyncio.run(capture_claim(
            db, statement="a claim that must be retrievable", task_ids=["instance_x"],
        ))
        node = db.knowledge_nodes[node_id]
        assert node["embedding"] is not None
        assert node["embedding"].startswith("[") and node["embedding"].endswith("]")
        assert len(node["embedding"].split(",")) == 1024

    def test_structured_fields_are_validated_and_stored(self):
        """Real check on ticket 03/10's NODE_TYPE_SCHEMAS registry: the
        new structured fields actually reach properties, validated."""
        db = FakeDB()
        db.add_task_node("instance_x")
        node_id = asyncio.run(capture_claim(
            db, statement="the auth module requires a valid token",
            task_ids=["instance_x"],
            subject="auth module", predicate="requires", object="valid token",
            claim_type="requirement", extraction_version="v1",
            epistemic_status="observed",
        ))
        props = db.knowledge_nodes[node_id]["properties"]
        assert props["subject"] == "auth module"
        assert props["predicate"] == "requires"
        assert props["object"] == "valid token"
        assert props["claim_type"] == "requirement"
        assert props["extraction_version"] == "v1"
        assert props["epistemic_status"] == "observed"

    def test_invalid_epistemic_status_is_rejected(self):
        """NODE_TYPE_SCHEMAS validation actually fires -- a bad value
        fails loudly at write time, not silently at some later read."""
        db = FakeDB()
        db.add_task_node("instance_x")
        with pytest.raises(Exception):  # pydantic.ValidationError
            asyncio.run(capture_claim(
                db, statement="bad status", task_ids=["instance_x"],
                epistemic_status="guessed",  # not 'observed' or 'inferred'
            ))

    def test_confidence_out_of_range_is_rejected(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        with pytest.raises(Exception):
            asyncio.run(capture_claim(
                db, statement="bad confidence", task_ids=["instance_x"],
                confidence=1.5,
            ))


class TestRelateClaims:
    def test_supersedes_writes_an_edge_and_flips_truth_state_out(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))
        new_id = asyncio.run(capture_claim(db, statement="new claim", task_ids=["instance_x"]))
        asyncio.run(relate_claims(
            db, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES",
        ))
        assert db.knowledge_nodes[old_id]["properties"]["truth_state"] == "OUT"
        # The new claim is untouched -- only the SUPERSEDED one flips.
        assert db.knowledge_nodes[new_id]["properties"]["truth_state"] == "IN"
        edge = [e for e in db.edges if e["custom_edge_type"] == "SUPERSEDES"][0]
        assert edge["source_id"] == UUID(new_id)
        assert edge["target_id"] == UUID(old_id)

    def test_contradicts_also_flips_truth_state_out(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(relate_claims(db, from_claim_id=b, to_claim_id=a, relation="CONTRADICTS"))
        assert db.knowledge_nodes[a]["properties"]["truth_state"] == "OUT"
        edge = [e for e in db.edges if e["custom_edge_type"] == "CONTRADICTS"][0]
        assert edge["edge_type"] == "SUPERSEDES"  # the enum bucket; CONTRADICTS is the refinement

    def test_the_original_claim_row_is_not_invalidated_only_its_truth_state(self):
        """t_invalid is a bi-temporal concern (does this row still exist);
        truth_state is a TMS concern (do we still believe it). A
        superseded claim must stay queryable as history."""
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))
        new_id = asyncio.run(capture_claim(db, statement="new claim", task_ids=["instance_x"]))
        asyncio.run(relate_claims(db, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES"))
        assert old_id in db.knowledge_nodes  # still present, not deleted

    def test_invalid_relation_is_rejected(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(relate_claims(
                db, from_claim_id=str(uuid4()), to_claim_id=str(uuid4()),
                relation="AGREES_WITH",
            ))
