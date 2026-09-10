"""
DB-free coverage for app/services/publication_deps.py (B11 / G24).

`traverse_publication_dependencies` walks the whole publication lineage
spec A14/§34 names -- procedures -> claims -> observations -> sources ->
artifacts -> evidence -- and returns a per-kind blocking verdict. These
tests drive it with a routing FakePool (same "fakes are hand-rolled per
file" convention as test_claim_evidence_offline.py) and prove:

  * every one of the six object kinds is reached and shaped as documented;
  * a private claim / observation / source / evidence row is `blocking`;
  * a clean all-public lineage produces `blocking == []`;
  * a lone public execution_result is NOT blocking but is flagged
    `independent=False` with the `private_evidence_not_global_verification`
    note (A14: private evidence is not global verification);
  * a claim referenced twice is deduped;
  * hitting the node bound appends a `traversal_truncated` blocking entry
    (fail closed);
  * a missing table degrades that ONE leg to `[]` (graceful degradation),
    never crashing the gate.
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services import publication_deps
from app.services.publication_deps import traverse_publication_dependencies


def _run(coro):
    return asyncio.run(coro)


class FakePool:
    """Routes each read by a SQL substring to a pre-seeded list. A leg
    whose key is set to the sentinel `_RAISE` raises UndefinedTableError,
    modelling a database where migrations 50-55 are not applied."""

    _RAISE = object()

    def __init__(self, **legs):
        self.legs = legs
        self.proc_ctx_id = legs.pop("proc_ctx_id", "ctx-1") if "proc_ctx_id" in legs else "ctx-1"
        self.seen: list[str] = []

    def _match(self, f):
        table_map = [
            ("FROM procedure_dependencies", "procedure_dependencies"),
            ("FROM procedure_claim_refs", "procedure_claim_refs"),
            ("FROM knowledge_nodes", "knowledge_nodes"),
            ("FROM claim_sources", "claim_sources"),
            ("FROM observations", "observations"),
            ("FROM ingestion_contexts", "ingestion_contexts"),
            ("FROM ingested_artifacts", "ingested_artifacts"),
            ("FROM artifact_blocks", "artifact_blocks"),
            ("FROM sources", "sources"),
            ("FROM evidence", "evidence"),
        ]
        for needle, key in table_map:
            if needle in f:
                return key
        raise AssertionError("unexpected SQL: " + f[:100])

    async def fetch(self, sql, *a):
        f = " ".join(sql.split())
        key = self._match(f)
        self.seen.append(key)
        val = self.legs.get(key, [])
        if val is FakePool._RAISE:
            raise asyncpg.exceptions.UndefinedTableError(f"relation {key} does not exist")
        return list(val)

    async def fetchval(self, sql, *a):
        f = " ".join(sql.split())
        if "ingestion_context_id FROM procedures" in f:
            return self.proc_ctx_id
        raise AssertionError("unexpected fetchval: " + f[:100])


C1 = "00000000-0000-4000-8000-0000000000c1"
C2 = "00000000-0000-4000-8000-0000000000c2"
O1 = "00000000-0000-4000-8000-0000000000d1"
S1 = "00000000-0000-4000-8000-0000000000e1"


def _traverse(pool):
    return _run(traverse_publication_dependencies(
        pool, procedure_row_id="prow-1", procedure_id="p-1", procedure_version=1,
    ))


# --------------------------------------------------------------- shape


def test_returns_all_six_object_kinds_with_counts():
    pool = FakePool(
        procedure_dependencies=[{"dependency_ref": "sub/x", "resolution_status": "resolved",
                                 "target_procedure_id": "t-1", "target_visibility": "public",
                                 "target_scope_type": "global", "target_name": "helper"}],
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION", "ingestion_context_id": None}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {"source_ref": S1}}],
        claim_sources=[{"claim_id": C1, "observation_id": O1}],
        observations=[{"id": O1, "visibility": "public", "owner_id": None,
                       "ingestion_context_id": "ctx-1"}],
        ingestion_contexts=[{"id": "ctx-1", "source_ref": S1}],
        ingested_artifacts=[{"id": "art-1", "visibility": "public", "source_ref": S1,
                             "ingestion_context_id": "ctx-1"}],
        artifact_blocks=[{"id": "blk-1", "visibility": "public"}],
        sources=[{"id": S1, "visibility": "public", "scope_type": "global",
                  "reliability_score": 0.9}],
        evidence=[{"id": "ev-1", "evidence_type": "human_review", "visibility": "public",
                   "independence_group": None, "outcome_status": None, "direction": "supports"}],
    )
    out = _traverse(pool)

    for kind in ("procedures", "claims", "observations", "sources", "artifacts", "evidence"):
        assert kind in out and len(out[kind]) >= 1, kind
    # artifacts is the ingested_artifact row PLUS its artifact_block
    assert out["counts"] == {"procedures": 1, "claims": 1, "observations": 1,
                             "sources": 1, "artifacts": 2, "evidence": 1}
    assert out["blocking"] == []
    assert out["classification"] == "PUBLIC"
    assert out["claims"][0]["role"] == "PRECONDITION"
    assert out["verification"]["verification_inherited"] is False


def test_all_public_lineage_is_not_blocking():
    pool = FakePool(
        procedure_dependencies=[{"dependency_ref": "sub/x", "resolution_status": "resolved",
                                 "target_procedure_id": "t-1", "target_visibility": "public",
                                 "target_scope_type": "global", "target_name": "h"}],
        procedure_claim_refs=[{"claim_id": C1, "role": "ASSUMPTION"}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {}}],
    )
    out = _traverse(pool)
    assert out["blocking"] == []
    assert out["classification"] == "PUBLIC"


# --------------------------------------------------------------- blocking


def test_private_claim_is_blocking():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[{"id": C1, "visibility": "private", "scope_type": "user",
                          "properties": {}}],
    )
    out = _traverse(pool)
    assert out["claims"][0]["blocking"] is True
    assert any(b["kind"] == "claim" and b["id"] == C1 for b in out["blocking"])
    assert out["classification"] == "MIXED"


def test_private_observation_is_blocking():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {}}],
        claim_sources=[{"claim_id": C1, "observation_id": O1}],
        observations=[{"id": O1, "visibility": "private", "owner_id": "alice",
                       "ingestion_context_id": None}],
    )
    out = _traverse(pool)
    assert out["observations"][0]["blocking"] is True
    assert any(b["kind"] == "observation" for b in out["blocking"])


def test_unresolved_claim_ref_fails_closed():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[],  # ref points nowhere live
    )
    out = _traverse(pool)
    assert out["claims"][0]["blocking"] is True
    assert any("fail closed" in b["reason"] for b in out["blocking"])


def test_unresolved_source_ref_is_blocking():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {"source_ref": S1}}],
        sources=[],  # S1 does not resolve
    )
    out = _traverse(pool)
    assert any(b["kind"] == "source" and b["id"] == S1 for b in out["blocking"])


def test_low_reliability_source_is_blocking():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {"source_ref": S1}}],
        sources=[{"id": S1, "visibility": "public", "scope_type": "global",
                  "reliability_score": 0.05}],
    )
    out = _traverse(pool)
    assert out["sources"][0]["blocking"] is True
    assert any("trust floor" in b["reason"] for b in out["blocking"])


# --------------------------------------------------------------- evidence


def test_private_execution_evidence_is_blocking():
    pool = FakePool(
        evidence=[{"id": "ev-1", "evidence_type": "execution_result", "visibility": "private",
                   "independence_group": None, "outcome_status": "success",
                   "direction": "supports"}],
    )
    out = _traverse(pool)
    assert out["evidence"][0]["blocking"] is True
    assert any(b["kind"] == "evidence" for b in out["blocking"])


def test_lone_public_execution_evidence_is_not_independent_and_notes_it():
    pool = FakePool(
        evidence=[{"id": "ev-1", "evidence_type": "execution_result", "visibility": "public",
                   "independence_group": None, "outcome_status": "success",
                   "direction": "supports"}],
    )
    out = _traverse(pool)
    assert out["evidence"][0]["blocking"] is False
    assert out["evidence"][0]["independent"] is False
    assert "private_evidence_not_global_verification" in out["notes"]
    assert out["verification"]["independent_public_verification"] is False
    assert out["verification"]["global_verification_required"] is True


def test_two_independent_public_execution_rows_are_independent():
    pool = FakePool(
        evidence=[
            {"id": "ev-1", "evidence_type": "execution_result", "visibility": "public",
             "independence_group": "run-a", "outcome_status": "success", "direction": "supports"},
            {"id": "ev-2", "evidence_type": "reproduction", "visibility": "public",
             "independence_group": "run-b", "outcome_status": "success", "direction": "supports"},
        ],
    )
    out = _traverse(pool)
    assert all(e["independent"] for e in out["evidence"])
    assert out["verification"]["independent_public_verification"] is True
    assert out["verification"]["global_verification_required"] is False
    assert "private_evidence_not_global_verification" not in out["notes"]


# --------------------------------------------------------------- dedupe / bounds / degradation


def test_same_claim_referenced_twice_is_deduped():
    pool = FakePool(
        procedure_claim_refs=[
            {"claim_id": C1, "role": "PRECONDITION"},
            {"claim_id": C1, "role": "RATIONALE"},
        ],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {}}],
    )
    out = _traverse(pool)
    assert [c["id"] for c in out["claims"]] == [C1]


def test_bound_hit_appends_traversal_truncated():
    big = [
        {"dependency_ref": f"sub/{i}", "resolution_status": "resolved",
         "target_procedure_id": f"t-{i}", "target_visibility": "public",
         "target_scope_type": "global", "target_name": "h"}
        for i in range(publication_deps.MAX_TRAVERSAL_NODES + 5)
    ]
    pool = FakePool(procedure_dependencies=big)
    out = _traverse(pool)
    assert any(b["reason"] == "traversal_truncated" for b in out["blocking"])
    assert out["classification"] == "MIXED"


def test_missing_table_leg_degrades_to_empty():
    pool = FakePool(
        procedure_claim_refs=[{"claim_id": C1, "role": "PRECONDITION"}],
        knowledge_nodes=[{"id": C1, "visibility": "public", "scope_type": "global",
                          "properties": {}}],
        artifact_blocks=FakePool._RAISE,       # migration 55 not applied
        sources=FakePool._RAISE,               # migration 50 not applied
    )
    out = _traverse(pool)
    assert out["artifacts"] == []
    assert out["sources"] == []
    assert out["blocking"] == []  # a missing leg is "no edges", not a blocker
