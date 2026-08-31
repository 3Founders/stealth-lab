"""
Tests for claims.py: claim-level hyper-nodes on the existing schema.

FakeDB mirrors the pool/connection dual-interface style already used in
test_failure_capture.py and test_knowledge_conflict_and_supersession.py,
scoped to the exact queries capture_claim()/relate_claims() issue.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.services.claims import (
    ALL_CLAIM_RELATIONS,
    CLAIM_STALE_THRESHOLD_DAYS,
    CREATED_BY,
    GENERAL_RELATIONS,
    capture_claim as _real_capture_claim,
    get_claim_lifecycle_state,
    get_claim_relations,
    get_claim_version_chain,
    link_claims,
    relate_claims,
    supersede_claim as _real_supersede_claim,
)


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


async def supersede_claim(db, **kwargs):
    """Same wrapper idiom as capture_claim() above -- supersede_claim()
    forwards its embedder kwarg straight into the capture_claim() call
    it reuses internally."""
    return await _real_supersede_claim(db, embedder=FakeEmbedder(), **kwargs)


class FakeDB:
    def __init__(self):
        self.task_nodes: dict[str, dict] = {}
        self.knowledge_nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.episode_links: list[dict] = []
        # get_claim_lifecycle_state's disputed check (has_open_conflict_
        # trigger) joins edges -> triggers -> debates; modeled minimally
        # here, just enough to drive that one predicate.
        self.triggers: dict[str, dict] = {}
        self.debates: dict[str, dict] = {}

    def add_task_node(self, skill_ref: str, *, invalid: bool = False) -> str:
        tid = str(uuid4())
        self.task_nodes[tid] = {"skill_ref": skill_ref, "t_invalid": "x" if invalid else None}
        return tid

    def add_trigger(self, task_node_id: str) -> str:
        trig_id = str(uuid4())
        self.triggers[trig_id] = {"task_node_id": str(task_node_id)}
        return trig_id

    def add_debate(self, trigger_id: str, state: str) -> str:
        debate_id = str(uuid4())
        self.debates[debate_id] = {"trigger_id": trigger_id, "state": state}
        return debate_id

    def add_conflict_edge(self, task_node_id: str, claim_id: str) -> None:
        """The VALIDATED_BY/CONFLICTS_WITH edge `_DISPUTED_CLAIM_SQL`
        looks for -- task_nodes -> knowledge_nodes, same shape
        knowledge_conflict.py's TriggerDetector writes for real."""
        self.edges.append({
            "edge_type": "VALIDATED_BY", "custom_edge_type": "CONFLICTS_WITH",
            "source_id": UUID(task_node_id), "source_table": "task_nodes",
            "target_id": UUID(claim_id), "target_table": "knowledge_nodes",
            "properties": {}, "created_by": "test",
        })

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
        if q.startswith("SELECT e.id, e.source_id, e.target_id, e.custom_edge_type"):
            # get_claim_relations(): claim_id is always $1, wanted-relations
            # list is always $2 -- direction is baked into which OR-clause(s)
            # the real query text contains, so match on substring presence.
            claim_id, wanted = params
            outgoing_ok = "e.source_id = $1::uuid" in q
            incoming_ok = "e.target_id = $1::uuid" in q
            wanted_set = set(wanted)
            out = []
            for e in self.edges:
                if e["source_table"] != "knowledge_nodes" or e["target_table"] != "knowledge_nodes":
                    continue
                if e["custom_edge_type"] not in wanted_set:
                    continue
                is_out = outgoing_ok and str(e["source_id"]) == str(claim_id)
                is_in = incoming_ok and str(e["target_id"]) == str(claim_id)
                if is_out or is_in:
                    out.append({
                        "id": uuid4(), "source_id": e["source_id"], "target_id": e["target_id"],
                        "relation": e["custom_edge_type"], "created_by": e["created_by"],
                        "t_valid": None, "properties": e["properties"],
                    })
            return out
        if q.startswith("SELECT id, properties FROM knowledge_nodes"):
            # get_claim_version_chain(): family_id passed once, bound to
            # $1 twice in the real query (id = $1::uuid OR properties->>
            # 'claim_family_id' = $1::text) -- FakeDB only receives the
            # one positional param either way.
            (family_id,) = params
            out = []
            for nid, node in self.knowledge_nodes.items():
                if node["node_type"] != "claim":
                    continue
                if nid == str(family_id) or node["properties"].get("claim_family_id") == str(family_id):
                    out.append({"id": UUID(nid), "properties": node["properties"]})
            out.sort(key=lambda r: r["properties"].get("claim_version", 1))
            return out
        if q.startswith("SELECT id, name FROM procedures"):
            # claim_impact.find_procedures_referencing_claim(), called by
            # relate_claims()'s real impact-propagation step -- this
            # FakeDB models no procedures at all, so the honest answer is
            # always "nothing references this claim".
            return []
        raise AssertionError(f"FakeDB.fetch: unrecognized query\n{q}")

    async def fetchrow(self, query: str, *params):
        q = query.strip()
        if q.startswith("SELECT properties, t_valid FROM knowledge_nodes"):
            (claim_id,) = params
            node = self.knowledge_nodes.get(str(claim_id))
            if node is None or node["node_type"] != "claim":
                return None
            return {"properties": node["properties"], "t_valid": node.get("t_valid")}
        if q.startswith("SELECT properties FROM knowledge_nodes"):
            (claim_id,) = params
            node = self.knowledge_nodes.get(str(claim_id))
            if node is None or node["node_type"] != "claim":
                return None
            return {"properties": node["properties"]}
        raise AssertionError(f"FakeDB.fetchrow: unrecognized query\n{q}")

    async def fetchval(self, query: str, *params):
        q = query.strip()
        if q.startswith("INSERT INTO knowledge_nodes"):
            (name, properties, embedding, created_by, owner_id, visibility,
             scope_type, scope_entity_id) = params
            nid = str(uuid4())
            self.knowledge_nodes[nid] = {
                "id": UUID(nid), "node_type": "claim", "name": name,
                "properties": dict(properties), "embedding": embedding,
                "created_by": created_by, "owner_id": owner_id,
                "visibility": visibility,
                "scope_type": scope_type, "scope_entity_id": scope_entity_id,
                # Fresh by default -- a test wanting a `stale` fixture
                # overwrites this directly on db.knowledge_nodes[nid].
                "t_valid": datetime.now(timezone.utc),
            }
            return UUID(nid)
        if q.startswith("SELECT EXISTS (SELECT 1 FROM edges e") and "triggers t" in q:
            # has_open_conflict_trigger()'s _DISPUTED_CLAIM_SQL: an open,
            # unresolved conflict trigger against this claim.
            (claim_id,) = params
            for e in self.edges:
                if not (
                    e["custom_edge_type"] == "CONFLICTS_WITH"
                    and e["edge_type"] == "VALIDATED_BY"
                    and e["source_table"] == "task_nodes"
                    and e["target_table"] == "knowledge_nodes"
                    and str(e["target_id"]) == str(claim_id)
                ):
                    continue
                task_node_id = str(e["source_id"])
                for trig_id, trig in self.triggers.items():
                    if trig["task_node_id"] != task_node_id:
                        continue
                    debates = [d for d in self.debates.values() if d["trigger_id"] == trig_id]
                    if not debates:
                        return True
                    if any(d["state"] not in ("APPROVED", "REJECTED") for d in debates):
                        return True
            return False
        if q.startswith("SELECT EXISTS (SELECT 1 FROM edges WHERE source_table = 'knowledge_nodes'"):
            # get_claim_lifecycle_state()'s retired/contradicted checks --
            # a live SUPERSEDES or CONTRADICTS edge targeting this claim.
            (claim_id,) = params
            wanted_relation = "SUPERSEDES" if "'SUPERSEDES'" in q else "CONTRADICTS"
            return any(
                e["source_table"] == "knowledge_nodes"
                and e["target_table"] == "knowledge_nodes"
                and str(e["target_id"]) == str(claim_id)
                and e["custom_edge_type"] == wanted_relation
                for e in self.edges
            )
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
        if q.startswith("INSERT INTO edges") and "$1::edge_type" in q:
            # relate_claims(): edge_type is now a real parameter, not an
            # inline literal -- SUPERSEDES for relation='SUPERSEDES',
            # VALIDATED_BY for CONTRADICTS (_edge_type_for_relation).
            edge_type, relation, from_id, to_id, properties, created_by = params
            self.edges.append({
                "edge_type": edge_type, "custom_edge_type": relation,
                "source_id": UUID(from_id), "source_table": "knowledge_nodes",
                "target_id": UUID(to_id), "target_table": "knowledge_nodes",
                "properties": properties, "created_by": created_by,
            })
            return "INSERT 0 1"
        if q.startswith("INSERT INTO edges") and "'VALIDATED_BY'::edge_type" in q:
            # link_claims(): always VALIDATED_BY, custom_edge_type carries
            # the general relation (SUPPORTS/REFINES/etc.).
            relation, from_id, to_id, properties, created_by = params
            self.edges.append({
                "edge_type": "VALIDATED_BY", "custom_edge_type": relation,
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
        # REAL BUG FIXED (Phase 0 audit of the consolidated directive):
        # this used to be edge_type='SUPERSEDES' unconditionally,
        # indistinguishable from a real SUPERSEDES edge by edge_type
        # alone. CONTRADICTS has no matching edge_type ENUM member (the
        # enum is frozen), so it rides the VALIDATED_BY bucket instead --
        # the same idiom this file's own CONFLICTS_WITH edges already use.
        assert edge["edge_type"] == "VALIDATED_BY"

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


class TestLinkClaims:
    """CONSOLIDATED directive Phase 2: general epistemic/structural claim
    relations, deliberately separate from relate_claims()'s Truth
    Maintenance semantics."""

    def test_supports_writes_an_edge_without_touching_truth_state(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=b, to_claim_id=a, relation="SUPPORTS"))
        edge = [e for e in db.edges if e["custom_edge_type"] == "SUPPORTS"][0]
        assert edge["source_id"] == UUID(b)
        assert edge["target_id"] == UUID(a)
        assert edge["edge_type"] == "VALIDATED_BY"
        # The defining difference from relate_claims(): no truth_state
        # side effect on either claim.
        assert db.knowledge_nodes[a]["properties"]["truth_state"] == "IN"
        assert db.knowledge_nodes[b]["properties"]["truth_state"] == "IN"

    def test_every_general_relation_in_the_directive_vocabulary_is_accepted(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        for relation in GENERAL_RELATIONS:
            asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation=relation))
        stored = {
            e["custom_edge_type"] for e in db.edges
            if e["source_id"] == UUID(a) and e["target_table"] == "knowledge_nodes"
        }
        assert stored == GENERAL_RELATIONS

    def test_supersedes_is_not_a_valid_link_claims_relation(self):
        """SUPERSEDES/CONTRADICTS are relate_claims()'s exclusively --
        link_claims() must refuse them rather than silently accept a
        truth-maintenance relation with no truth_state side effect."""
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(link_claims(
                db, from_claim_id=str(uuid4()), to_claim_id=str(uuid4()),
                relation="SUPERSEDES",
            ))

    def test_invalid_relation_is_rejected(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(link_claims(
                db, from_claim_id=str(uuid4()), to_claim_id=str(uuid4()),
                relation="AGREES_WITH",
            ))

    def test_properties_are_stored_when_given(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(link_claims(
            db, from_claim_id=a, to_claim_id=b, relation="DEPENDS_ON",
            properties={"reason": "pandas>=2.0 removes DataFrame.append"},
        ))
        edge = [e for e in db.edges if e["custom_edge_type"] == "DEPENDS_ON"][0]
        assert edge["properties"]["reason"] == "pandas>=2.0 removes DataFrame.append"


class TestGetClaimRelations:
    def test_returns_outgoing_and_incoming_by_default(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        c = asyncio.run(capture_claim(db, statement="claim C", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation="SUPPORTS"))
        asyncio.run(link_claims(db, from_claim_id=c, to_claim_id=a, relation="REFINES"))

        relations = asyncio.run(get_claim_relations(db, a))
        found = {(r["relation"], str(r["source_id"]), str(r["target_id"])) for r in relations}
        assert found == {("SUPPORTS", a, b), ("REFINES", c, a)}

    def test_direction_outgoing_only(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        c = asyncio.run(capture_claim(db, statement="claim C", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation="SUPPORTS"))
        asyncio.run(link_claims(db, from_claim_id=c, to_claim_id=a, relation="REFINES"))

        relations = asyncio.run(get_claim_relations(db, a, direction="outgoing"))
        assert len(relations) == 1
        assert relations[0]["relation"] == "SUPPORTS"

    def test_direction_incoming_only(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        c = asyncio.run(capture_claim(db, statement="claim C", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation="SUPPORTS"))
        asyncio.run(link_claims(db, from_claim_id=c, to_claim_id=a, relation="REFINES"))

        relations = asyncio.run(get_claim_relations(db, a, direction="incoming"))
        assert len(relations) == 1
        assert relations[0]["relation"] == "REFINES"

    def test_relations_filter_narrows_the_result(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation="SUPPORTS"))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=b, relation="GENERALIZES"))

        relations = asyncio.run(get_claim_relations(db, a, relations={"SUPPORTS"}))
        assert len(relations) == 1
        assert relations[0]["relation"] == "SUPPORTS"

    def test_mixing_truth_maintenance_and_general_relations_both_findable(self):
        """RELATIONS (SUPERSEDES/CONTRADICTS) and GENERAL_RELATIONS live
        in different edge_type buckets internally but must both be
        queryable uniformly through this one reader -- that's the whole
        point of ALL_CLAIM_RELATIONS."""
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        c = asyncio.run(capture_claim(db, statement="claim C", task_ids=["instance_x"]))
        asyncio.run(relate_claims(db, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS"))
        asyncio.run(link_claims(db, from_claim_id=a, to_claim_id=c, relation="SUPPORTS"))

        relations = asyncio.run(get_claim_relations(db, a, direction="outgoing"))
        found = {r["relation"] for r in relations}
        assert found == {"CONTRADICTS", "SUPPORTS"}

    def test_invalid_direction_is_rejected(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(get_claim_relations(db, str(uuid4()), direction="sideways"))

    def test_unknown_relation_filter_is_rejected(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(get_claim_relations(db, str(uuid4()), relations={"AGREES_WITH"}))

    def test_all_claim_relations_is_the_real_union(self):
        assert ALL_CLAIM_RELATIONS == {
            "SUPERSEDES", "CONTRADICTS", "SUPPORTS", "REFINES", "DEPENDS_ON",
            "CONDITIONAL_ON", "GENERALIZES", "SPECIALIZES", "DERIVED_FROM",
            "INSTANTIATES", "APPLIES_TO",
        }


class TestSupersedeClaim:
    """Claims get the same version-chain concept procedures already have
    (family_id + version), but living inside properties JSONB rather than
    real columns -- claims share knowledge_nodes with 6 other virtual
    node types, so no migration for this."""

    def test_first_supersession_starts_a_new_family_rooted_at_the_prior_claim(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))

        new_id = asyncio.run(supersede_claim(
            db, prior_claim_id=old_id, statement="new claim", task_ids=["instance_x"],
        ))

        assert new_id is not None
        assert new_id != old_id
        new_props = db.knowledge_nodes[new_id]["properties"]
        assert new_props["claim_family_id"] == old_id
        assert new_props["claim_version"] == 2
        # The prior claim itself carries no family_id yet -- it IS the
        # family root, implicitly version 1.
        assert "claim_family_id" not in db.knowledge_nodes[old_id]["properties"]

    def test_reuses_relate_claims_to_link_and_flip_truth_state(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))

        new_id = asyncio.run(supersede_claim(
            db, prior_claim_id=old_id, statement="new claim", task_ids=["instance_x"],
        ))

        assert db.knowledge_nodes[old_id]["properties"]["truth_state"] == "OUT"
        assert db.knowledge_nodes[new_id]["properties"]["truth_state"] == "IN"
        edge = [e for e in db.edges if e["custom_edge_type"] == "SUPERSEDES"][0]
        assert edge["source_id"] == UUID(new_id)
        assert edge["target_id"] == UUID(old_id)

    def test_second_supersession_increments_version_and_keeps_the_same_family(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        v1 = asyncio.run(capture_claim(db, statement="v1", task_ids=["instance_x"]))
        v2 = asyncio.run(supersede_claim(
            db, prior_claim_id=v1, statement="v2", task_ids=["instance_x"],
        ))
        v3 = asyncio.run(supersede_claim(
            db, prior_claim_id=v2, statement="v3", task_ids=["instance_x"],
        ))

        v3_props = db.knowledge_nodes[v3]["properties"]
        assert v3_props["claim_family_id"] == v1  # same root as v2
        assert v3_props["claim_version"] == 3

    def test_reason_is_recorded_on_the_new_claim_when_given(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))
        new_id = asyncio.run(supersede_claim(
            db, prior_claim_id=old_id, statement="new claim", task_ids=["instance_x"],
            reason="pandas 2.0 removed DataFrame.append",
        ))
        assert db.knowledge_nodes[new_id]["properties"]["supersession_reason"] == (
            "pandas 2.0 removed DataFrame.append"
        )

    def test_missing_prior_claim_raises(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(supersede_claim(
                db, prior_claim_id=str(uuid4()), statement="orphan supersession",
                task_ids=["instance_x"],
            ))

    def test_statement_and_structured_fields_reach_the_new_claim(self):
        """supersede_claim reuses capture_claim -- it must not lose any
        of capture_claim's own fields in the process."""
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))
        new_id = asyncio.run(supersede_claim(
            db, prior_claim_id=old_id, statement="new claim", task_ids=["instance_x"],
            subject="s", predicate="p", object="o", confidence=0.9,
        ))
        props = db.knowledge_nodes[new_id]["properties"]
        assert props["statement"] == "new claim"
        assert props["subject"] == "s"
        assert props["predicate"] == "p"
        assert props["object"] == "o"
        assert props["confidence"] == 0.9


class TestGetClaimVersionChain:
    def test_single_version_claim_returns_only_itself(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        only_id = asyncio.run(capture_claim(db, statement="only version", task_ids=["instance_x"]))

        chain = asyncio.run(get_claim_version_chain(db, only_id))
        assert [str(r["id"]) for r in chain] == [only_id]

    def test_chain_is_returned_oldest_to_newest(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        v1 = asyncio.run(capture_claim(db, statement="v1", task_ids=["instance_x"]))
        v2 = asyncio.run(supersede_claim(db, prior_claim_id=v1, statement="v2", task_ids=["instance_x"]))
        v3 = asyncio.run(supersede_claim(db, prior_claim_id=v2, statement="v3", task_ids=["instance_x"]))

        chain = asyncio.run(get_claim_version_chain(db, v1))
        assert [str(r["id"]) for r in chain] == [v1, v2, v3]
        assert [r["properties"]["statement"] for r in chain] == ["v1", "v2", "v3"]

    def test_chain_is_the_same_regardless_of_which_version_id_is_queried(self):
        db = FakeDB()
        db.add_task_node("instance_x")
        v1 = asyncio.run(capture_claim(db, statement="v1", task_ids=["instance_x"]))
        v2 = asyncio.run(supersede_claim(db, prior_claim_id=v1, statement="v2", task_ids=["instance_x"]))
        v3 = asyncio.run(supersede_claim(db, prior_claim_id=v2, statement="v3", task_ids=["instance_x"]))

        from_root = asyncio.run(get_claim_version_chain(db, v1))
        from_middle = asyncio.run(get_claim_version_chain(db, v2))
        from_tip = asyncio.run(get_claim_version_chain(db, v3))
        assert [str(r["id"]) for r in from_root] == [v1, v2, v3]
        assert [str(r["id"]) for r in from_middle] == [v1, v2, v3]
        assert [str(r["id"]) for r in from_tip] == [v1, v2, v3]


class TestGetClaimLifecycleState:
    """One real fixture per reachable lifecycle state -- each constructed
    through the real underlying signals (relate_claims/link_claims/direct
    edge+trigger+debate fixtures), never by mocking
    get_claim_lifecycle_state's own internals. `proposed` is exempt: the
    function's own docstring documents it as not currently reachable from
    real data, and there is no test for it here on purpose."""

    def test_missing_claim_raises(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(get_claim_lifecycle_state(db, str(uuid4())))

    def test_default_case_is_current(self):
        """truth_state IN, no dispute, no reaffirming relations, fresh
        t_valid -- the overwhelmingly common real case."""
        db = FakeDB()
        db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id))
        assert state == "current"

    def test_open_conflict_trigger_makes_it_disputed(self):
        """Real signal: an open, unresolved trigger (no debate at all)
        against a live, truth_state=IN claim -- has_open_conflict_trigger
        reused verbatim, precedence position 1."""
        db = FakeDB()
        task_id = db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="disputed claim", task_ids=["instance_x"]))
        db.add_conflict_edge(task_id, claim_id)
        db.add_trigger(task_id)  # no debate -> unresolved

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id))
        assert state == "disputed"

    def test_open_debate_also_makes_it_disputed(self):
        """A trigger with a debate that hasn't reached APPROVED/REJECTED
        is still open, same as no debate at all."""
        db = FakeDB()
        task_id = db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="disputed claim 2", task_ids=["instance_x"]))
        db.add_conflict_edge(task_id, claim_id)
        trig_id = db.add_trigger(task_id)
        db.add_debate(trig_id, "OPEN")

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id))
        assert state == "disputed"

    def test_resolved_debate_is_not_disputed(self):
        """A trigger whose debate concluded APPROVED/REJECTED is resolved
        -- has_open_conflict_trigger correctly returns False, so this
        falls through to `current`."""
        db = FakeDB()
        task_id = db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="resolved claim", task_ids=["instance_x"]))
        db.add_conflict_edge(task_id, claim_id)
        trig_id = db.add_trigger(task_id)
        db.add_debate(trig_id, "APPROVED")

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id))
        assert state == "current"

    def test_superseded_claim_is_retired(self):
        """Real signal: truth_state OUT via relate_claims(SUPERSEDES) --
        a live SUPERSEDES edge targets this claim, precedence position 2."""
        db = FakeDB()
        db.add_task_node("instance_x")
        old_id = asyncio.run(capture_claim(db, statement="old claim", task_ids=["instance_x"]))
        new_id = asyncio.run(capture_claim(db, statement="new claim", task_ids=["instance_x"]))
        asyncio.run(relate_claims(db, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES"))

        state = asyncio.run(get_claim_lifecycle_state(db, old_id))
        assert state == "retired"

    def test_contradicted_claim_is_contradicted(self):
        """Real signal: truth_state OUT via relate_claims(CONTRADICTS) --
        a live CONTRADICTS edge targets this claim, no SUPERSEDES edge,
        precedence position 3."""
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(relate_claims(db, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS"))

        state = asyncio.run(get_claim_lifecycle_state(db, b))
        assert state == "contradicted"

    def test_out_with_no_edge_falls_back_to_retired(self):
        """Defensive fallback: truth_state flipped OUT (e.g. by direct
        properties mutation, bypassing relate_claims) with neither a
        SUPERSEDES nor CONTRADICTS edge present -- documented as
        shouldn't-happen-in-practice, and this proves the fallback branch
        without crashing the caller."""
        db = FakeDB()
        db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="orphan OUT claim", task_ids=["instance_x"]))
        db.knowledge_nodes[claim_id]["properties"]["truth_state"] = "OUT"

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id))
        assert state == "retired"

    def test_old_unreaffirmed_claim_is_stale(self):
        """Real signal: t_valid older than CLAIM_STALE_THRESHOLD_DAYS
        (relative to a controlled `as_of`) with no incoming SUPPORTS/
        GENERALIZES/DERIVED_FROM edge -- precedence position 4. Uses a
        fake t_valid + controlled as_of, not a real 180-day wait."""
        db = FakeDB()
        db.add_task_node("instance_x")
        claim_id = asyncio.run(capture_claim(db, statement="old unsupported claim", task_ids=["instance_x"]))
        as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.knowledge_nodes[claim_id]["t_valid"] = as_of - timedelta(days=CLAIM_STALE_THRESHOLD_DAYS + 1)

        state = asyncio.run(get_claim_lifecycle_state(db, claim_id, as_of=as_of))
        assert state == "stale"

    def test_old_but_reaffirmed_claim_is_supported_not_stale(self):
        """Same age as the stale fixture above, but with a live incoming
        SUPPORTS edge -- reaffirmation beats staleness, precedence
        position 5 wins over position 4."""
        db = FakeDB()
        db.add_task_node("instance_x")
        old_claim = asyncio.run(capture_claim(db, statement="old but reaffirmed claim", task_ids=["instance_x"]))
        supporter = asyncio.run(capture_claim(db, statement="supporting claim", task_ids=["instance_x"]))
        as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.knowledge_nodes[old_claim]["t_valid"] = as_of - timedelta(days=CLAIM_STALE_THRESHOLD_DAYS + 1)
        asyncio.run(link_claims(db, from_claim_id=supporter, to_claim_id=old_claim, relation="SUPPORTS"))

        state = asyncio.run(get_claim_lifecycle_state(db, old_claim, as_of=as_of))
        assert state == "supported"

    def test_fresh_claim_with_reaffirmation_is_supported(self):
        """Real signal: at least one live incoming SUPPORTS/GENERALIZES/
        DERIVED_FROM edge, fresh t_valid (not stale at all) --
        precedence position 5."""
        db = FakeDB()
        db.add_task_node("instance_x")
        base = asyncio.run(capture_claim(db, statement="base claim", task_ids=["instance_x"]))
        derived = asyncio.run(capture_claim(db, statement="derived claim", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=derived, to_claim_id=base, relation="DERIVED_FROM"))

        state = asyncio.run(get_claim_lifecycle_state(db, base))
        assert state == "supported"

    def test_non_reaffirming_relation_does_not_count_as_supported(self):
        """DEPENDS_ON is a real GENERAL_RELATIONS member but not in the
        reaffirming subset -- a claim with only a DEPENDS_ON incoming
        edge and fresh t_valid stays `current`."""
        db = FakeDB()
        db.add_task_node("instance_x")
        a = asyncio.run(capture_claim(db, statement="claim A", task_ids=["instance_x"]))
        b = asyncio.run(capture_claim(db, statement="claim B", task_ids=["instance_x"]))
        asyncio.run(link_claims(db, from_claim_id=b, to_claim_id=a, relation="DEPENDS_ON"))

        state = asyncio.run(get_claim_lifecycle_state(db, a))
        assert state == "current"
