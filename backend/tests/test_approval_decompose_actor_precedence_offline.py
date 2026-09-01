"""Regression test for a real identity-spoofing gap in the two named-
human-approval write paths (CLAUDE.md: "apply only after a named human
approves"): `app/api/approval.py::decide` and
`app/api/decompose.py::decide`. Both wrote the request body's
self-asserted `approver_id` straight into `approvals`/`decompositions`
attribution and into `KnowledgeUpdater.apply()`'s own `approved_by`, with
no cross-check against the validated OIDC actor
(`app.services.authn.current_actor_id()`) -- the same rule
`app/api/deps.py::get_scope` documents and `app/api/agent_store.py`
already follows: a validated actor always overrides a body-asserted one;
the self-asserted value survives only as the unauthenticated fallback.

Proven here: with a validated actor set on the contextvar, a caller who
puts a DIFFERENT identity in `approver_id` cannot make it into any of the
recorded attribution -- before the fix, the spoofed body value won
outright, letting anyone claim to be the approving human on record.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api import approval, decompose
from app.services.authn import Actor, reset_current_actor, set_current_actor

SCORECARD_ID = UUID("00000000-0000-4000-8000-000000000501")
DEBATE_ID = UUID("00000000-0000-4000-8000-000000000601")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000701")
DECOMPOSITION_ID = UUID("00000000-0000-4000-8000-000000000801")


@pytest.fixture(autouse=True)
def _clean_actor_contextvar():
    yield
    reset_current_actor(set_current_actor(None))


# ---------------------------------------------------------------------
# approval.py::decide
# ---------------------------------------------------------------------


class _FakeApprovalPool:
    def __init__(self):
        self.fetchrow_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        self.fetchrow_calls.append((sql, params))
        if "FROM scorecards" in sql:
            return {
                "id": SCORECARD_ID, "debate_id": DEBATE_ID, "candidate_id": CANDIDATE_ID,
                "layer1_passed": True, "blast_radius": 1, "reversible": True,
                "recommendation": "approve", "summary": "s", "change_set": {"ops": []},
                "supporters": [],
            }
        if "INSERT INTO approvals" in sql:
            return {"id": UUID("00000000-0000-4000-8000-000000000901")}
        raise AssertionError(f"unexpected fetchrow: {sql}")


class _FakeDebateStateMachine:
    def __init__(self, pool):
        self.transition_calls: list[dict] = []

    async def current_state(self, debate_id):
        return "PENDING_APPROVAL"

    async def transition(self, debate_id, to_state, *, reason, actor):
        self.transition_calls.append({"actor": actor, "reason": reason})


class _FakeKnowledgeUpdater:
    def __init__(self, pool):
        self.apply_calls: list[tuple] = []

    async def apply(self, change_set, approver_id, at):
        self.apply_calls.append((approver_id, at))
        return []


def test_approval_decide_prefers_validated_actor_over_spoofed_body(monkeypatch):
    fake_machine = _FakeDebateStateMachine(pool=None)
    fake_updater = _FakeKnowledgeUpdater(pool=None)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: fake_machine)
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: fake_updater)

    pool = _FakeApprovalPool()
    body = approval.ApprovalRequest(
        approver_id="attacker-claims-to-be-admin", decision="approved",
    )

    tok = set_current_actor(Actor(subject="real-validated-user"))
    try:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    finally:
        reset_current_actor(tok)

    assert fake_updater.apply_calls[0][0] == "real-validated-user"
    assert fake_machine.transition_calls[0]["actor"] == "real-validated-user"
    insert_sql, insert_params = pool.fetchrow_calls[1]
    assert "INSERT INTO approvals" in insert_sql
    assert insert_params[2] == "real-validated-user"
    assert "attacker-claims-to-be-admin" not in (
        fake_updater.apply_calls[0][0],
        fake_machine.transition_calls[0]["actor"],
        insert_params[2],
    )


def test_approval_decide_falls_back_to_body_when_unauthenticated(monkeypatch):
    fake_machine = _FakeDebateStateMachine(pool=None)
    fake_updater = _FakeKnowledgeUpdater(pool=None)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: fake_machine)
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: fake_updater)

    pool = _FakeApprovalPool()
    body = approval.ApprovalRequest(approver_id="anonymous-caller", decision="approved")

    asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))

    assert fake_updater.apply_calls[0][0] == "anonymous-caller"


# ---------------------------------------------------------------------
# decompose.py::decide
# ---------------------------------------------------------------------


class _FakeDecomposePool:
    def __init__(self):
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        return {
            "id": DECOMPOSITION_ID, "change_set": {"ops": []},
            "status": "proposed", "feasible": True,
        }

    async def execute(self, sql: str, *params):
        self.execute_calls.append((sql, params))


class _FakeKnowledgeUpdaterGenerated:
    def __init__(self, pool):
        self.apply_generated_calls: list[str] = []

    async def apply_generated(self, change_set, approver_id):
        self.apply_generated_calls.append(approver_id)
        return {"applied": [], "refs": {}}


def test_decompose_decide_prefers_validated_actor_over_spoofed_body(monkeypatch):
    fake_updater = _FakeKnowledgeUpdaterGenerated(pool=None)
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: fake_updater)

    pool = _FakeDecomposePool()
    body = decompose.DecideRequest(
        approver_id="attacker-claims-to-be-admin", decision="approved",
    )

    tok = set_current_actor(Actor(subject="real-validated-user"))
    try:
        asyncio.run(decompose.decide(DECOMPOSITION_ID, body, pool=pool))
    finally:
        reset_current_actor(tok)

    assert fake_updater.apply_generated_calls[0] == "real-validated-user"
    update_sql, update_params = pool.execute_calls[0]
    assert "UPDATE decompositions" in update_sql
    assert update_params[2] == "real-validated-user"


def test_decompose_decide_falls_back_to_body_when_unauthenticated(monkeypatch):
    fake_updater = _FakeKnowledgeUpdaterGenerated(pool=None)
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: fake_updater)

    pool = _FakeDecomposePool()
    body = decompose.DecideRequest(approver_id="anonymous-caller", decision="approved")

    asyncio.run(decompose.decide(DECOMPOSITION_ID, body, pool=pool))

    assert fake_updater.apply_generated_calls[0] == "anonymous-caller"
