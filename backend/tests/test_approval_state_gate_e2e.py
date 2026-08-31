"""
Real, live-database, end-to-end proving test for a real bug found and fixed
during this session's P1 apply/execution-safety audit
(backend/app/api/approval.py::decide):

Before the fix, `decide()` called `KnowledgeUpdater.apply()`
UNCONDITIONALLY, before ever checking whether the debate's own state
actually supported the requested decision -- the real state-machine gate
(`DebateStateMachine.transition()`) only ran AFTER the write. That meant
re-deciding an already-decided (or otherwise not-PENDING_APPROVAL)
scorecard still applied the change_set -- for a `create_edge` op (no
natural idempotency guard, unlike `invalidate_edge`/`update_*_node`'s
t_invalid-IS-NULL checks) this silently duplicated real graph content --
and only THEN failed with 409 from the state transition, after the
damage was already done. This is the exact "gate and writer together"
half-gate CLAUDE.md's own hard rule 6 names.

NOTE: `KnowledgeUpdater.apply()` (the plain debate-approval path this
test exercises) only ever handles `InvalidateEdgeOp`/`CreateEdgeOp`/
`UpdateTaskNodeOp`/`UpdateKnowledgeNodeOp` -- `CreateTaskNodeOp`/
`CreateKnowledgeNodeOp` are the *generative decomposition* path's ops
(`apply_generated()`, a different method), never legal input to `apply()`
(confirmed by reading knowledge_update.py's own exhaustive isinstance
chain). This test therefore proposes a `create_edge` op between two real,
pre-existing task_nodes -- a legal, ordinary `apply()` op -- not a
create_task_node op.

This test proves the fix: calling `decide()` on a scorecard whose debate
is already APPROVED (not PENDING_APPROVAL) is refused with 409 BEFORE any
write -- no new edge is created, no second `approvals` row is inserted.

Same live-DB convention as test_procedures_e2e.py / test_mcp_server_
identity_e2e.py: requires a real DATABASE_URL, skips (not fails) without
one.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import HTTPException

from app.api.approval import ApprovalRequest, decide
from app.db.session import create_pool

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _make_task_node(pool, name: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name) VALUES ($1) RETURNING id", name,
    )
    return str(row["id"])


async def _make_debate_with_scorecard(pool, *, debate_state: str, task_node_id: str, target_task_node_id: str):
    """Build a real trigger -> debate -> candidate -> scorecard chain, with
    the debate already sitting in `debate_state` (bypassing the state
    machine directly via SQL is legitimate here: this test is proving what
    `decide()` does when it is CALLED AGAINST an already-resolved/illegal
    debate state, which is exactly the scenario a retried/replayed decide()
    call, or a second concurrent approver, produces for real).

    The change set proposes a `create_edge` op between two real,
    pre-existing task_nodes -- the plain `apply()` path's real, legal op
    (see this module's own docstring for why `create_task_node` is NOT
    legal input here)."""
    trigger_row = await pool.fetchrow(
        "INSERT INTO triggers (task_node_id, rule_name, metric_name, "
        "observed_value, threshold, sample_size) "
        "VALUES ($1, 'test_rule', 'error_rate', 1.0, 0.5, 10) RETURNING id",
        task_node_id,
    )
    trigger_id = trigger_row["id"]

    debate_row = await pool.fetchrow(
        "INSERT INTO debates (trigger_id, state) VALUES ($1, $2::debate_state) RETURNING id",
        trigger_id, debate_state,
    )
    debate_id = debate_row["id"]

    change_set = {
        "ops": [
            {
                "op_type": "create_edge",
                "edge_type": "REQUIRES",
                "source_id": task_node_id,
                "source_table": "task_nodes",
                "target_id": target_task_node_id,
                "target_table": "task_nodes",
            }
        ]
    }
    candidate_row = await pool.fetchrow(
        "INSERT INTO candidates (debate_id, summary, rationale, change_set) "
        "VALUES ($1, 'test candidate', 'test rationale', $2) RETURNING id",
        debate_id, change_set,
    )
    candidate_id = candidate_row["id"]

    scorecard_row = await pool.fetchrow(
        "INSERT INTO scorecards (debate_id, candidate_id, layer1_passed, "
        "constructive, groundedness_score, recommendation) "
        "VALUES ($1, $2, true, true, 1.0, 'approve') RETURNING id",
        debate_id, candidate_id,
    )
    return debate_id, candidate_id, scorecard_row["id"]


async def _cleanup(pool, task_node_id: str, target_task_node_id: str) -> None:
    # REAL FK ORDER, found live: candidates/scorecards DO cascade from
    # debates (ON DELETE CASCADE, db/02_loop.sql), but `approvals`
    # references scorecard_id/candidate_id WITHOUT a cascade -- a real
    # `decide(approved)` call in this test writes an approvals row that
    # would otherwise block the cascade delete below with a real
    # ForeignKeyViolationError. Delete approvals explicitly first.
    await pool.execute(
        "DELETE FROM approvals WHERE scorecard_id IN ("
        "  SELECT s.id FROM scorecards s JOIN debates d ON d.id = s.debate_id "
        "  JOIN triggers t ON t.id = d.trigger_id WHERE t.task_node_id = $1"
        ")", task_node_id,
    )
    await pool.execute(
        "DELETE FROM debates WHERE trigger_id IN "
        "(SELECT id FROM triggers WHERE task_node_id = $1)", task_node_id,
    )
    await pool.execute("DELETE FROM triggers WHERE task_node_id = $1", task_node_id)
    await pool.execute(
        "DELETE FROM edges WHERE source_id = $1 OR target_id = $1 "
        "OR source_id = $2 OR target_id = $2",
        task_node_id, target_task_node_id,
    )
    await pool.execute(
        "DELETE FROM task_nodes WHERE id = ANY($1::uuid[])",
        [task_node_id, target_task_node_id],
    )


def test_decide_refuses_before_applying_when_debate_is_not_pending_approval():
    """The real regression proof: a scorecard whose debate is already
    APPROVED (simulating a re-decided/replayed decide() call) must be
    refused with 409 BEFORE `updater.apply()` ever runs -- no edge
    created, no `approvals` row inserted. Before the fix, this exact call
    sequence created a real edge and a real `approvals` row, and only
    failed afterward on the state transition."""

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        task_node_id = await _make_task_node(pool, "gate-test-task-node")
        target_task_node_id = await _make_task_node(pool, "gate-test-target-node")
        try:
            debate_id, candidate_id, scorecard_id = await _make_debate_with_scorecard(
                pool, debate_state="APPROVED", task_node_id=task_node_id,
                target_task_node_id=target_task_node_id,
            )

            before_edges = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2",
                task_node_id, target_task_node_id,
            )
            before_approvals = await pool.fetchval(
                "SELECT count(*) FROM approvals WHERE scorecard_id = $1", scorecard_id,
            )
            assert before_edges == 0
            assert before_approvals == 0

            body = ApprovalRequest(approver_id="test-approver", decision="approved")
            with pytest.raises(HTTPException) as exc_info:
                await decide(scorecard_id, body, pool=pool)
            assert exc_info.value.status_code == 409

            after_edges = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2",
                task_node_id, target_task_node_id,
            )
            after_approvals = await pool.fetchval(
                "SELECT count(*) FROM approvals WHERE scorecard_id = $1", scorecard_id,
            )
            assert after_edges == 0, (
                "decide() must refuse BEFORE applying the change_set when the "
                "debate is not in a state that legally supports this decision "
                "-- a real edge here means the pre-fix apply-before-gate bug "
                "regressed"
            )
            assert after_approvals == 0, (
                "no approvals audit row should be written for a refused decision"
            )
        finally:
            await _cleanup(pool, task_node_id, target_task_node_id)
            await pool.close()

    asyncio.run(_run())


def test_decide_applies_normally_when_debate_is_pending_approval():
    """Honest counterpart: the fix must not break the real, legal path --
    a debate genuinely in PENDING_APPROVAL still applies normally on
    approval."""

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        task_node_id = await _make_task_node(pool, "gate-test-task-node-legal")
        target_task_node_id = await _make_task_node(pool, "gate-test-target-node-legal")
        try:
            debate_id, candidate_id, scorecard_id = await _make_debate_with_scorecard(
                pool, debate_state="PENDING_APPROVAL", task_node_id=task_node_id,
                target_task_node_id=target_task_node_id,
            )

            body = ApprovalRequest(approver_id="test-approver", decision="approved")
            response = await decide(scorecard_id, body, pool=pool)

            assert response.decision == "approved"
            assert len(response.applied_ops) == 1
            assert response.applied_ops[0]["op"] == "create_edge"

            created_count = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2",
                task_node_id, target_task_node_id,
            )
            assert created_count == 1

            state = await pool.fetchval(
                "SELECT state::text FROM debates WHERE id = $1", debate_id,
            )
            assert state == "APPROVED"
        finally:
            await _cleanup(pool, task_node_id, target_task_node_id)
            await pool.close()

    asyncio.run(_run())


def test_decide_refuses_re_approval_of_an_already_approved_scorecard_without_double_applying():
    """The realistic, deterministic reproduction of the original bug: call
    decide(approved) twice in a row against the SAME scorecard (a client
    retry, a double-click, or a second approver racing the first -- all
    real, ordinary scenarios). The first call must succeed and apply once;
    the second call must be refused BEFORE applying again -- exactly one
    edge must exist afterward, not two, and exactly one approvals row,
    not two."""

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        task_node_id = await _make_task_node(pool, "gate-test-task-node-redecide")
        target_task_node_id = await _make_task_node(pool, "gate-test-target-node-redecide")
        try:
            debate_id, candidate_id, scorecard_id = await _make_debate_with_scorecard(
                pool, debate_state="PENDING_APPROVAL", task_node_id=task_node_id,
                target_task_node_id=target_task_node_id,
            )

            body = ApprovalRequest(approver_id="first-approver", decision="approved")
            first = await decide(scorecard_id, body, pool=pool)
            assert first.decision == "approved"

            body2 = ApprovalRequest(approver_id="second-approver", decision="approved")
            with pytest.raises(HTTPException) as exc_info:
                await decide(scorecard_id, body2, pool=pool)
            assert exc_info.value.status_code == 409

            created_count = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE source_id = $1 AND target_id = $2",
                task_node_id, target_task_node_id,
            )
            assert created_count == 1, (
                "the second, re-decided call must not have applied the "
                "change_set a second time"
            )
            approvals_count = await pool.fetchval(
                "SELECT count(*) FROM approvals WHERE scorecard_id = $1", scorecard_id,
            )
            assert approvals_count == 1, (
                "the second, re-decided call must not have written a second "
                "approvals audit row"
            )
        finally:
            await _cleanup(pool, task_node_id, target_task_node_id)
            await pool.close()

    asyncio.run(_run())
