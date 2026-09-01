"""Regression test for a real identity-spoofing gap in
app/api/agent_store.py: `submit_agent`, `promote`, and `decide` wrote a
request body's self-asserted identity field (`submitted_by` / `actor`)
straight into `created_by`/`actor` write-path attribution, with no
cross-check against the validated OIDC actor (`app.services.authn.
current_actor_id()`) -- the exact rule `app/api/deps.py::get_scope` and
`app/mcp_server/server.py::_resolve_caller_identity` already document and
follow everywhere else: a validated actor always overrides a header/body-
asserted identity; the self-asserted value survives only as the fallback
for the unauthenticated posture.

Proven here: with a validated actor set on the contextvar, a caller who
puts a DIFFERENT identity in the request body cannot make it into the
`created_by`/`actor` value these endpoints act on -- before the fix, the
spoofed body value won outright.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api import agent_store
from app.services.authn import Actor, reset_current_actor, set_current_actor


@pytest.fixture(autouse=True)
def _clean_actor_contextvar():
    yield
    reset_current_actor(set_current_actor(None))


AGENT_ID = UUID("00000000-0000-4000-8000-000000000a91")


class _FakePool:
    """Captures the params of the one INSERT submit_agent issues."""

    def __init__(self, agent_id: UUID):
        self.agent_id = agent_id
        self.fetchrow_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        self.fetchrow_calls.append((sql, params))
        return {"id": self.agent_id}


class _FakeOrchestrator:
    def __init__(self, pool, panel):
        pass

    async def review_code_sourced(self, agent_id):
        return {"passed": True, "opinions": []}


class _FakeStateMachine:
    def __init__(self, pool):
        pass

    async def current_state(self, agent_id):
        return "pending_review"


def test_submit_agent_prefers_validated_actor_over_spoofed_body_field(monkeypatch):
    monkeypatch.setattr(agent_store, "default_panel", lambda: ["reviewer-a", "reviewer-b"])
    monkeypatch.setattr(agent_store, "CodeSourcedReviewOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(agent_store, "AgentReviewStateMachine", _FakeStateMachine)

    pool = _FakePool(AGENT_ID)
    body = agent_store.SubmitAgentRequest(
        name="n", description="d", source="user_submitted",
        submitted_by="attacker-claims-to-be-admin",
    )

    tok = set_current_actor(Actor(subject="real-validated-user"))
    try:
        asyncio.run(agent_store.submit_agent(body, pool=pool))
    finally:
        reset_current_actor(tok)

    sql, params = pool.fetchrow_calls[0]
    assert "created_by" in sql
    created_by = params[-1]
    assert created_by == "real-validated-user"
    assert created_by != "attacker-claims-to-be-admin"


def test_submit_agent_falls_back_to_body_field_when_unauthenticated(monkeypatch):
    monkeypatch.setattr(agent_store, "default_panel", lambda: ["reviewer-a", "reviewer-b"])
    monkeypatch.setattr(agent_store, "CodeSourcedReviewOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(agent_store, "AgentReviewStateMachine", _FakeStateMachine)

    pool = _FakePool(AGENT_ID)
    body = agent_store.SubmitAgentRequest(
        name="n", description="d", source="user_submitted",
        submitted_by="anonymous-caller",
    )

    asyncio.run(agent_store.submit_agent(body, pool=pool))

    sql, params = pool.fetchrow_calls[0]
    assert params[-1] == "anonymous-caller"


def test_promote_prefers_validated_actor_over_spoofed_body_field(monkeypatch):
    captured = {}

    async def fake_promote_decomposition(pool, decomposition_id, judge, *, actor):
        captured["actor"] = actor
        return {
            "agent_id": AGENT_ID,
            "review": SimpleNamespace(passed=True, notes=""),
        }

    monkeypatch.setattr(agent_store, "promote_decomposition", fake_promote_decomposition)
    monkeypatch.setattr(agent_store, "AgentReviewStateMachine", _FakeStateMachine)

    body = agent_store.PromoteRequest(
        decomposition_id=UUID("00000000-0000-4000-8000-000000000d01"),
        actor="attacker-claims-to-be-admin",
    )

    tok = set_current_actor(Actor(subject="real-validated-user"))
    try:
        asyncio.run(agent_store.promote(body, pool=object()))
    finally:
        reset_current_actor(tok)

    assert captured["actor"] == "real-validated-user"
    assert captured["actor"] != "attacker-claims-to-be-admin"


def test_decide_prefers_validated_actor_over_spoofed_body_field(monkeypatch):
    captured = {}

    async def fake_decide_agent(pool, agent_id, decision, registry, *, actor, reason,
                                 acknowledge_sandbox_limitations):
        captured["actor"] = actor
        return {"agent_id": agent_id, "review_state": "approved", "runnable": False}

    monkeypatch.setattr(agent_store, "decide_agent", fake_decide_agent)

    body = agent_store.DecideRequest(decision="approved", actor="attacker-claims-to-be-admin")

    tok = set_current_actor(Actor(subject="real-validated-user"))
    try:
        asyncio.run(agent_store.decide(AGENT_ID, body, pool=object()))
    finally:
        reset_current_actor(tok)

    assert captured["actor"] == "real-validated-user"
    assert captured["actor"] != "attacker-claims-to-be-admin"
