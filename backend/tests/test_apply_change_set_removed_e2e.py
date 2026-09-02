"""
Live-DB counterpart to test_apply_change_set_removed_security.py.

After the ungated `apply_change_set` MCP tool was removed (post-freeze
security hardening, v1-final-2026-09-03.1), the ONLY paths that mutate the
knowledge graph are the two gated approval endpoints. These tests drive a
REAL persisted proposal through each one against a real database and prove
the audit trail is intact:

  8. app/api/approval.py::decide -- a real PENDING_APPROVAL scorecard is
     applied and an `approvals` row is written carrying the actor, the
     applied ops (from the STORED change_set) and a timestamp.
  9. app/api/decompose.py::decide -- a real status='proposed' decomposition
     is applied and its row is updated with status='approved', approver_id
     and decided_at.

Same live-DB convention as test_approval_state_gate_e2e.py /
test_mcp_server_identity_e2e.py: requires a real DATABASE_URL, skips (not
fails) without one.
"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest

from app.api.approval import ApprovalRequest, decide as approval_decide
from app.api.decompose import DecideRequest, decide as decompose_decide
from app.db.session import create_pool
from tests.test_approval_state_gate_e2e import (
    _cleanup,
    _make_debate_with_scorecard,
    _make_task_node,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


# ---------------------------------------------------------------------
# 8: approval.decide -- real scorecard, real apply, real approvals row.
# ---------------------------------------------------------------------


def test_gated_approval_still_applies_and_writes_an_audit_row():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        src = await _make_task_node(pool, "acs-removed-e2e-src")
        tgt = await _make_task_node(pool, "acs-removed-e2e-tgt")
        try:
            debate_id, candidate_id, scorecard_id = await _make_debate_with_scorecard(
                pool, debate_state="PENDING_APPROVAL",
                task_node_id=src, target_task_node_id=tgt,
            )

            body = ApprovalRequest(approver_id="removed-e2e-approver", decision="approved")
            resp = await approval_decide(scorecard_id, body, pool=pool)

            assert resp.decision == "approved"
            assert len(resp.applied_ops) == 1
            assert resp.applied_ops[0]["op"] == "create_edge"

            audit = await pool.fetchrow(
                "SELECT approver_id, decision, decided_at, applied_at, applied_ops "
                "FROM approvals WHERE scorecard_id = $1", scorecard_id,
            )
            assert audit is not None, "an approvals audit row must exist"
            assert audit["approver_id"] == "removed-e2e-approver"
            assert audit["decision"] == "approved"
            assert audit["decided_at"] is not None
            assert audit["applied_at"] is not None
            ops = audit["applied_ops"]
            if isinstance(ops, str):
                ops = json.loads(ops)
            assert [o["op"] for o in ops] == ["create_edge"], (
                "the audit row records the ops from the STORED change_set"
            )

            edge_count = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2",
                src, tgt,
            )
            assert edge_count == 1

            state = await pool.fetchval(
                "SELECT state::text FROM debates WHERE id = $1", debate_id,
            )
            assert state == "APPROVED"
        finally:
            await _cleanup(pool, src, tgt)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# 9: decompose.decide -- real proposed decomposition, real apply, real
#    status/approver/decided_at update.
# ---------------------------------------------------------------------


def test_gated_decomposition_still_applies_and_records_the_decision():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        marker = f"acs-removed-e2e-{uuid4().hex[:12]}"
        change_set = {
            "ops": [
                {"op_type": "create_task_node", "ref": "t1",
                 "name": f"{marker}-extract", "description": "e2e", "io_schema": {},
                 "success_criteria": {}},
                {"op_type": "create_task_node", "ref": "t2",
                 "name": f"{marker}-validate", "description": "e2e", "io_schema": {},
                 "success_criteria": {}},
                {"op_type": "create_edge", "source_ref": "t1", "target_ref": "t2",
                 "edge_type": "PRODUCES"},
            ]
        }
        row = await pool.fetchrow(
            "INSERT INTO decompositions (submitter_key, problem, feasible, reasoning, "
            "change_set, structural_problems, objections, suspected_manipulation, "
            "input_flagged) VALUES ($1,$2,true,$3,$4,$5,$6,false,false) RETURNING id",
            f"key-{marker}", "e2e removed-tool decomposition", "split step",
            change_set, [], [],
        )
        decomp_id = row["id"]
        try:
            body = DecideRequest(approver_id="removed-e2e-approver", decision="approved")
            resp = await decompose_decide(decomp_id, body, pool=pool)

            assert resp.decision == "approved"
            assert len(resp.created_nodes) >= 2

            decided = await pool.fetchrow(
                "SELECT status, approver_id, decided_at FROM decompositions WHERE id = $1",
                decomp_id,
            )
            assert decided["status"] == "approved"
            assert decided["approver_id"] == "removed-e2e-approver"
            assert decided["decided_at"] is not None

            made = await pool.fetchval(
                "SELECT count(*) FROM task_nodes WHERE name LIKE $1 "
                "AND provenance = 'public_generated'",
                f"{marker}-%",
            )
            assert made == 2

            # Idempotency: a second decide on the now-'approved' row is
            # refused with 409 and does not create a second subgraph.
            with pytest.raises(Exception) as exc:
                await decompose_decide(decomp_id, body, pool=pool)
            assert getattr(exc.value, "status_code", None) == 409
            made_again = await pool.fetchval(
                "SELECT count(*) FROM task_nodes WHERE name LIKE $1 "
                "AND provenance = 'public_generated'",
                f"{marker}-%",
            )
            assert made_again == 2, "re-decide must not double-apply"
        finally:
            node_ids = await pool.fetch(
                "SELECT id FROM task_nodes WHERE name LIKE $1", f"{marker}-%",
            )
            ids = [r["id"] for r in node_ids]
            if ids:
                await pool.execute(
                    "DELETE FROM edges WHERE source_id = ANY($1::uuid[]) "
                    "OR target_id = ANY($1::uuid[])", ids,
                )
                await pool.execute("DELETE FROM task_nodes WHERE id = ANY($1::uuid[])", ids)
            await pool.execute("DELETE FROM decompositions WHERE id = $1", decomp_id)
            await pool.close()

    asyncio.run(_run())
