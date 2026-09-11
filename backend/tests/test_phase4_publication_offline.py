"""Phase 4 (launch compliance) — the canonical publication gate.

Offline: a routing FakePool answers every statement PublicationService
emits. Proves: only the owner of a PRIVATE/ORG row can publish; a secret /
private-dependency / provenance-less source is refused with reasons and
NO global row; a clean source creates a fresh candidate + a
publication_records row + audit events, and carries NO evidence; and
withdrawal picks an outcome from lineage without rewriting evidence.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.services.publication import (
    PublicationDenied,
    SourceProcedureNotFound,
    publish_procedure,
    withdraw_publication,
)


class FakePool:
    def __init__(self, *, src=None, deps=(), impls=(), pubrec=None, independent=0,
                 claim_refs=(), claim_nodes=(), claim_sources=(), observations=(),
                 ingestion_contexts=(), ingested_artifacts=(), artifact_blocks=(),
                 sources=(), evidence=(), proc_ctx_id=None):
        self.src = src
        self.deps = list(deps)
        self.impls = list(impls)
        self.pubrec = pubrec
        self.independent = independent
        # B11/G24 full-graph traversal legs -- default empty so every
        # pre-existing test keeps its exact behaviour (an empty leg is a
        # non-blocking leg).
        self.claim_refs = list(claim_refs)
        self.claim_nodes = list(claim_nodes)
        self.claim_sources = list(claim_sources)
        self.observations = list(observations)
        self.ingestion_contexts = list(ingestion_contexts)
        self.ingested_artifacts = list(ingested_artifacts)
        self.artifact_blocks = list(artifact_blocks)
        self.sources = list(sources)
        self.evidence = list(evidence)
        self.proc_ctx_id = proc_ctx_id
        self.captured = None            # kwargs passed to the procedures INSERT
        self.audits = []               # (action, object_type, object_id)
        self.updates = []
        self.pubrec_insert_args = None  # args bound to the publication_records INSERT

    def _flat(self, sql):
        return " ".join(sql.split())

    async def fetchrow(self, sql, *a):
        f = self._flat(sql)
        if f.startswith("SELECT * FROM procedures WHERE id"):
            return self.src
        if f.startswith("SELECT * FROM publication_records WHERE id"):
            return self.pubrec
        if "INSERT INTO procedures" in f:
            self.captured = (f, a)
            return {"id": str(uuid4()), "procedure_id": str(uuid4())}
        if "INSERT INTO audit_events" in f:
            # $3 action, $4 object_type, $5 object_id
            self.audits.append((a[2], a[3], a[4]))
            return {"id": len(self.audits)}
        raise AssertionError("unexpected fetchrow: " + f[:80])

    async def fetch(self, sql, *a):
        f = self._flat(sql)
        if "FROM procedure_dependencies" in f:
            return self.deps
        if "FROM procedure_implementations" in f:
            return self.impls
        if "FROM procedure_claim_refs" in f:
            return self.claim_refs
        if "FROM knowledge_nodes" in f:
            return self.claim_nodes
        if "FROM claim_sources" in f:
            return self.claim_sources
        if "FROM observations" in f:
            return self.observations
        if "FROM ingestion_contexts" in f:
            return self.ingestion_contexts
        if "FROM ingested_artifacts" in f:
            return self.ingested_artifacts
        if "FROM artifact_blocks" in f:
            return self.artifact_blocks
        if "FROM sources" in f:
            return self.sources
        if "FROM evidence" in f:
            return self.evidence
        raise AssertionError("unexpected fetch: " + f[:80])

    async def fetchval(self, sql, *a):
        f = self._flat(sql)
        if "INSERT INTO publication_records" in f:
            self.pubrec_insert_args = a
            return "pub-uuid-1"
        if "independent_success_count" in f:
            return self.independent
        if "ingestion_context_id FROM procedures" in f:
            return self.proc_ctx_id
        raise AssertionError("unexpected fetchval: " + f[:80])

    async def execute(self, sql, *a):
        self.updates.append((self._flat(sql), a))
        return "UPDATE 1"

    # G24 residual: publish_procedure now writes a pending_global_
    # verifications row via tenant_transaction(), which needs a real
    # pool.acquire() -> conn.transaction() -> conn.execute(...) chain.
    # The "conn" this yields is this same FakePool -- its own execute()
    # above already accepts anything (permissive, unlike fetchrow/fetch).
    def acquire(self):
        return _AcquireCM(self)

    def transaction(self):
        return _NoopTxn()


class _NoopTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _AcquireCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _proc(**over):
    row = {
        "id": "src-1", "name": "deploy the thing", "goal": "ship it safely",
        "steps": [{"order": 0, "goal": "run tests"}], "preconditions": [],
        "invariants": [], "failure_conditions": [], "exclusions": [],
        "visibility": "private", "owner_id": "alice", "provenance": "system_pending_review",
        "verification_state": "candidate", "scope_type": "user",
        "domain": "devops", "domain_payload": {},
    }
    row.update(over)
    return row


def _dep(visibility="public", ref="sub/x", target="t-1", status="resolved"):
    return {
        "dependency_ref": ref, "resolution_status": status,
        "target_procedure_id": target, "target_visibility": visibility,
        "target_name": "helper",
    }


# ---------------------------------------------------------------- allow


def test_owner_publishes_a_clean_private_procedure():
    pool = FakePool(src=_proc(), deps=[_dep("public")])
    out = asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))

    assert out["scope"] == "GLOBAL CANDIDATE"
    assert out["verification"] == "candidate"
    assert out["publication_id"] == "pub-uuid-1"

    # the new row is public, has NO embedding / evidence forwarded
    ins_sql, ins_args = pool.captured
    assert "visibility" in ins_sql
    assert "public" in ins_args
    assert not any(isinstance(x, (bytes,)) for x in ins_args)  # no vector blob

    actions = {a[0] for a in pool.audits}
    assert "publication_approved" in actions
    assert "global_candidate_created" in actions


# ---------------------------------------------------------------- deny


def test_non_owner_is_refused_and_writes_no_global_row():
    pool = FakePool(src=_proc(owner_id="bob"), deps=[])
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("does not own" in r for r in ei.value.reasons)
    assert pool.captured is None
    assert ("publication_rejected", "procedure", "src-1") in pool.audits


def test_public_source_is_not_publishable():
    pool = FakePool(src=_proc(visibility="public"), deps=[])
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("not publishable" in r for r in ei.value.reasons)


def test_private_dependency_blocks_publication():
    pool = FakePool(src=_proc(), deps=[_dep(visibility="private", ref="sub/secret")])
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("PRIVATE" in r for r in ei.value.reasons)
    assert pool.captured is None


def test_missing_provenance_blocks_publication():
    pool = FakePool(src=_proc(provenance=None), deps=[])
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("provenance" in r for r in ei.value.reasons)


def test_surviving_secret_signal_blocks_publication():
    pool = FakePool(
        src=_proc(goal="run it with api_key=AKIA123SECRETVALUE in the env"),
        deps=[],
    )
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("credential" in r or "sanitization" in r for r in ei.value.reasons)


def test_unknown_source_is_404_shaped():
    pool = FakePool(src=None)
    with pytest.raises(SourceProcedureNotFound):
        asyncio.run(publish_procedure(pool, source_row_id="nope", actor_subject="alice"))


# ------------------------------------------------ B11/G24: full-graph deps

_CLAIM_UUID = "00000000-0000-4000-8000-0000000000a1"


def test_private_referenced_claim_blocks_publication():
    """B11/G24: the gate now walks procedure -> claims. A PRIVATE claim in
    the lineage is a disqualification even when every procedure dependency
    is public."""
    pool = FakePool(
        src=_proc(procedure_id="p-1", version=1),
        deps=[_dep("public")],
        claim_refs=[{"claim_id": _CLAIM_UUID, "role": "PRECONDITION",
                     "ingestion_context_id": None}],
        claim_nodes=[{"id": _CLAIM_UUID, "visibility": "private",
                      "scope_type": "user", "properties": {}}],
    )
    with pytest.raises(PublicationDenied) as ei:
        asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))
    assert any("claim" in r and _CLAIM_UUID in r for r in ei.value.reasons)
    assert pool.captured is None


def test_dependency_report_insert_carries_full_traversal():
    """The publication_records.dependency_report JSONB now holds the FULL
    traversal result (per-kind counts + blocking), not just the procedure
    counters."""
    import json

    pool = FakePool(src=_proc(procedure_id="p-1", version=1), deps=[_dep("public")])
    asyncio.run(publish_procedure(pool, source_row_id="src-1", actor_subject="alice"))

    dep_blob = json.loads(pool.pubrec_insert_args[8])
    assert "full_traversal" in dep_blob
    ft = dep_blob["full_traversal"]
    assert set(ft["counts"]) == {
        "procedures", "claims", "observations", "sources", "artifacts", "evidence"
    }
    assert ft["blocking"] == []
    assert ft["classification"] == "PUBLIC"


def test_clean_publish_records_verification_not_inherited():
    """A16: a clean publish still does NOT inherit private verification --
    classification_report says so and the return echoes it."""
    import json

    pool = FakePool(src=_proc(procedure_id="p-1", version=1), deps=[_dep("public")])
    out = asyncio.run(
        publish_procedure(pool, source_row_id="src-1", actor_subject="alice")
    )

    cls_report = json.loads(pool.pubrec_insert_args[9])
    assert cls_report["verification_inherited"] is False
    assert cls_report["global_verification_required"] is True
    assert out["verification"] == "candidate"
    assert out["verification_inherited"] is False


# ---------------------------------------------------------------- withdrawal


def _pubrec(**over):
    row = {
        "id": "pub-uuid-1", "actor_subject": "alice",
        "published_object_id": "glob-1",
        "dependency_report": {"classification": "PUBLIC"},
    }
    row.update(over)
    return row


def test_withdraw_with_independent_evidence_is_retained():
    pool = FakePool(pubrec=_pubrec(), independent=3)
    out = asyncio.run(withdraw_publication(pool, publication_id="pub-uuid-1", actor_subject="alice"))
    assert out["withdrawal_state"] == "RETAINED_AS_INDEPENDENTLY_SOURCED"


def test_withdraw_clean_no_evidence_is_removed_from_retrieval():
    """Real bug fix (found while hardening the sibling Local -> Global
    path, app/services/publish.py): 'withdrawn' is not a real
    procedure_availability enum value (active|quarantined|disabled,
    db/18_procedures.sql) -- the old UPDATE would have raised
    InvalidTextRepresentation against a real database the first time
    this ever ran live. 'disabled' is the correct existing value for
    "permanently excluded from retrieval"."""
    pool = FakePool(pubrec=_pubrec(), independent=0)
    out = asyncio.run(withdraw_publication(pool, publication_id="pub-uuid-1", actor_subject="alice"))
    assert out["withdrawal_state"] == "WITHDRAWN_FROM_RETRIEVAL"
    assert any("availability = 'disabled'" in u[0] for u in pool.updates)


def test_withdraw_mixed_lineage_requires_remediation():
    pool = FakePool(
        pubrec=_pubrec(dependency_report={"classification": "MIXED"}), independent=0
    )
    out = asyncio.run(withdraw_publication(pool, publication_id="pub-uuid-1", actor_subject="alice"))
    assert out["withdrawal_state"] == "REQUIRES_REMEDIATION"


def test_withdraw_by_non_creator_is_refused():
    pool = FakePool(pubrec=_pubrec(actor_subject="bob"))
    with pytest.raises(PublicationDenied):
        asyncio.run(withdraw_publication(pool, publication_id="pub-uuid-1", actor_subject="alice"))
