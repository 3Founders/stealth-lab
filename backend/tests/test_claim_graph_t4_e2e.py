"""
T4 -- claim-graph invariants against a real Postgres (V4-hardening
Testing §T4), focused on the properties this lane changed:

  * a Claim with valid provenance and ZERO task_nodes / zero episodes is
    valid (B7);
  * one Claim can be referenced by MANY Procedures via typed
    `procedure_claim_refs`, in different roles (B4/B5);
  * contradictory Claims coexist -- both rows live, linked by a
    CONTRADICTS edge, and the TMS flips only the target's truth_state;
  * a belief change CITES the evidence that caused it (B8) -- the
    ChangeSet `recompute_claim_belief` records names the evidence ids.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.claim_belief import recompute_claim_belief
from app.services.claim_evidence import record_claim_evidence
from app.services.claims import capture_claim, relate_claims
from app.services.procedure_claim_refs import add_procedure_claim_ref, list_procedures_for_claim

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database claim-graph test"
)

_MARK = "t4-claim-graph-e2e"


async def _cleanup(pool) -> None:
    claim_ids = [
        r["id"] for r in await pool.fetch(
            "SELECT id FROM knowledge_nodes WHERE node_type = 'claim' "
            "AND name LIKE $1", f"{_MARK}%"
        )
    ]
    for cid in claim_ids:
        await pool.execute("DELETE FROM procedure_claim_refs WHERE claim_id = $1", cid)
        await pool.execute("DELETE FROM claim_sources WHERE claim_id = $1", cid)
        await pool.execute(
            "DELETE FROM edges WHERE (source_id = $1 OR target_id = $1) "
            "AND (source_table = 'knowledge_nodes' OR target_table = 'knowledge_nodes')", cid
        )
        await pool.execute(
            "UPDATE evidence SET t_invalid = now() "
            "WHERE target_type = 'claim' AND target_id = $1 AND t_invalid IS NULL", cid
        )
    # change_sets is [H] append-only (DELETE rejected). The handful this
    # test records carry `_MARK` in their reason and are harmless residue.
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND name LIKE $1", f"{_MARK}%"
    )


def test_claim_with_document_provenance_and_zero_anchors_is_valid_and_multiref():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)

            # B7: no task_ids, no episode -- only a source_ref / observation
            # provenance ref. Must still be written.
            src_ref = str(uuid.uuid4())
            claim_id = await capture_claim(
                pool,
                statement=f"{_MARK}: symbol-level retrieval reduces irrelevant context",
                task_ids=[],
                source_ref=src_ref,
                created_by="test_claim_graph_t4",
                scope_type="global",
            )
            assert claim_id is not None, "B7: a document-provenanced claim must be written"

            row = await pool.fetchrow(
                "SELECT node_type, properties FROM knowledge_nodes "
                "WHERE id = $1 AND t_invalid IS NULL", claim_id
            )
            assert row is not None and row["node_type"] == "claim"
            props = row["properties"]
            if isinstance(props, str):
                props = json.loads(props)
            assert props.get("source_ref") == src_ref, "source_ref preserved on the claim"

            # zero task-edge: the claim supports nothing via PRODUCES/CLAIM_OF
            task_edges = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 "
                "AND custom_edge_type = 'CLAIM_OF'", claim_id
            )
            assert task_edges == 0

            # B4/B5: one Claim, many Procedures, different roles. The typed
            # `procedure_claim_refs` rows are what B4 is about -- they exist
            # independently of whether the procedure row is present.
            p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await add_procedure_claim_ref(
                pool, procedure_id=str(p1), procedure_version=1, claim_id=claim_id,
                role="PRECONDITION", ref_origin="authored", created_by="t4",
            )
            await add_procedure_claim_ref(
                pool, procedure_id=str(p2), procedure_version=2, claim_id=claim_id,
                role="RATIONALE", ref_origin="derived", created_by="t4",
            )
            await add_procedure_claim_ref(
                pool, procedure_id=str(p3), procedure_version=1, claim_id=claim_id,
                role="APPLICABILITY", ref_origin="authored", created_by="t4",
            )
            back = await pool.fetch(
                "SELECT procedure_id::text AS procedure_id, role FROM procedure_claim_refs "
                "WHERE claim_id = $1 AND t_invalid IS NULL", claim_id,
            )
            proc_ids = {r["procedure_id"] for r in back}
            assert {str(p1), str(p2), str(p3)} <= proc_ids, "one Claim -> many typed refs"
            roles = {r["role"] for r in back}
            assert {"PRECONDITION", "RATIONALE", "APPLICABILITY"} <= roles, "distinct roles on one Claim"
            # and the service reader (which requires a live procedure row) is
            # correctly empty here -- a ref to a non-existent procedure is not
            # a "procedure for this claim".
            assert await list_procedures_for_claim(pool, claim_id) == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_contradictory_claims_coexist_and_belief_change_cites_evidence():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)

            a = await capture_claim(
                pool, statement=f"{_MARK}: parallel test execution reduces wall-clock time",
                task_ids=[], source_ref=str(uuid.uuid4()),
                created_by="t4", scope_type="global",
            )
            b = await capture_claim(
                pool, statement=f"{_MARK}: parallel test execution does NOT reduce wall-clock time on this suite",
                task_ids=[], source_ref=str(uuid.uuid4()),
                created_by="t4", scope_type="global",
            )
            assert a and b and a != b

            await relate_claims(pool, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS")

            # both rows still LIVE (coexist); the edge exists; only b flipped OUT
            rows = await pool.fetch(
                "SELECT id, properties FROM knowledge_nodes WHERE id = ANY($1::uuid[]) "
                "AND t_invalid IS NULL", [a, b]
            )
            assert len(rows) == 2, "contradictory claims must both remain live"
            by_id = {}
            for r in rows:
                p = r["properties"]
                by_id[str(r["id"])] = json.loads(p) if isinstance(p, str) else p
            assert by_id[a].get("truth_state", "IN") == "IN"
            assert by_id[b].get("truth_state") == "OUT", "CONTRADICTS flips only the target"

            # relate_claims writes CONTRADICTS as edge_type='VALIDATED_BY' +
            # custom_edge_type='CONTRADICTS' (only SUPERSEDES uses the real
            # enum member).
            edge = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2 "
                "AND custom_edge_type = 'CONTRADICTS'", a, b
            )
            assert edge >= 1

            # B8: recording evidence + recomputing belief cites the evidence.
            ev_id = await record_claim_evidence(
                pool, claim_id=a, evidence_type="execution_result",
                outcome_status="success", success_criteria={"predicate": "suite ran 2.1x faster"},
                created_by="t4",
            )
            belief = await recompute_claim_belief(
                pool, a, changeset_reason=f"{_MARK}: evidence recorded",
            )
            assert isinstance(belief, dict)
            assert ev_id in (belief.get("cited_evidence_ids") or []), (
                "the belief result must cite the evidence it aggregated"
            )
            # the ChangeSet it recorded also carries the cited ids
            cs = await pool.fetchrow(
                "SELECT cso.detail FROM change_sets cs "
                "JOIN change_set_operations cso ON cso.change_set_id = cs.id "
                "WHERE cs.reason LIKE $1 AND cso.target_id = $2 "
                "ORDER BY cs.created_at DESC LIMIT 1",
                f"%{_MARK}%", a,
            )
            assert cs is not None, "recompute must record a ChangeSet"
            detail = cs["detail"]
            if isinstance(detail, str):
                detail = json.loads(detail)
            assert ev_id in (detail.get("cited_evidence_ids") or []), (
                "the belief ChangeSet detail must name the evidence"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
