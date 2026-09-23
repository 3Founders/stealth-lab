"""
Offline (no real DB) regression tests for the B1-B4 bypass-closure pass.
Same direct-call convention test_goals_api_offline.py established:
`Depends()` params overridden with explicit keyword args, service layer
monkeypatched to capture what it was actually called with. DB-dependent
invariants (idempotent completion, cross-goal execution rejection, the
full accept -> freeze / accept -> associate flows) live in
tests/test_bypass_closure_e2e.py, gated on DATABASE_URL.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.api.goals as goals_api
from app.api.deps import AuthenticatedPrincipal
from app.services.access import AccessScope

PRINCIPAL = AuthenticatedPrincipal(user_id="user-alice", subject="sub-alice", email="alice@example.com")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# B1 -- /v1/solutions/associate can only ever create a 'proposed' row, and
# identity/attribution is never taken from the request body.
# ---------------------------------------------------------------------------

def test_B1_associate_solution_forces_proposed_status_and_server_identity(monkeypatch):
    captured = {}

    async def fake_associate(pool, **kw):
        captured.update(kw)
        return {"id": "sol-1", **kw}

    async def fake_fetchrow(*a, **kw):
        return None  # no existing row -> not an "already active" case

    monkeypatch.setattr(goals_api.pm, "associate_solution", fake_associate)
    fake_pool = type("P", (), {"fetchrow": staticmethod(fake_fetchrow)})()

    body = goals_api.SolutionIn(
        goal_id="goal-1", solution_type="procedure", target_id="proc-1",
        status="active", proposer="mallory",
    )
    result = _run(goals_api.associate_solution(body, pool=fake_pool, scope=AccessScope.unrestricted(), principal=PRINCIPAL))

    assert captured["status"] == "proposed", "a direct client call must never create an 'active' association"
    assert captured["proposer"] == PRINCIPAL.subject, "attribution must be server-derived, never the client's 'proposer' field"
    assert result["id"] == "sol-1"


def test_B1_associate_solution_never_downgrades_an_existing_active_row(monkeypatch):
    """A client re-posting the same association must not be able to
    silently un-list an already-accepted Procedure/Benchmark."""
    existing_active = {"id": "sol-1", "goal_id": "goal-1", "solution_type": "procedure",
                        "target_id": "proc-1", "version": 1, "status": "active", "proposer": "sub-bob"}

    async def fake_fetchrow(*a, **kw):
        return existing_active

    called = {"associate": False}

    async def fake_associate(pool, **kw):
        called["associate"] = True
        return {}

    monkeypatch.setattr(goals_api.pm, "associate_solution", fake_associate)
    fake_pool = type("P", (), {"fetchrow": staticmethod(fake_fetchrow)})()

    body = goals_api.SolutionIn(goal_id="goal-1", solution_type="procedure", target_id="proc-1", status="proposed")
    result = _run(goals_api.associate_solution(body, pool=fake_pool, scope=AccessScope.unrestricted(), principal=PRINCIPAL))

    assert result == existing_active
    assert called["associate"] is False, "an existing 'active' row must be echoed back untouched, never re-written by this route"


def test_B1_goal_solutions_endpoint_filters_to_active_only(monkeypatch):
    rows = [
        {"id": "s1", "status": "active", "target_table": "procedures"},
        {"id": "s2", "status": "proposed", "target_table": "procedures"},
    ]

    async def fake_list(pool, goal_id, *, scope):
        return rows

    monkeypatch.setattr(goals_api.pm, "list_goal_solutions", fake_list)
    result = _run(goals_api.goal_solutions("goal-1", pool=object(), scope=AccessScope.unrestricted()))
    assert [s["id"] for s in result["solutions"]] == ["s1"]


# ---------------------------------------------------------------------------
# B3 -- freeze_benchmark requires an accepted benchmark_submissions row.
# ---------------------------------------------------------------------------

def test_B3_freeze_benchmark_refuses_without_an_accepted_submission(monkeypatch):
    async def fake_fetchval(*a, **kw):
        return None  # no accepted benchmark_submissions row found

    fake_pool = type("P", (), {"fetchval": staticmethod(fake_fetchval)})()

    with pytest.raises(HTTPException) as exc_info:
        _run(goals_api.freeze_benchmark("bench-1", pool=fake_pool, scope=AccessScope.unrestricted(), principal=PRINCIPAL))
    assert exc_info.value.status_code == 409


def test_B3_freeze_benchmark_proceeds_and_audits_when_accepted(monkeypatch):
    async def fake_fetchval(*a, **kw):
        return "sub-1"

    async def fake_freeze(pool, benchmark_id):
        return {"id": benchmark_id, "status": "frozen"}

    audited = {}

    async def fake_audit(pool, **kw):
        audited.update(kw)
        return "audit-1"

    fake_pool = type("P", (), {"fetchval": staticmethod(fake_fetchval)})()
    monkeypatch.setattr(goals_api.pm, "freeze_benchmark", fake_freeze)
    monkeypatch.setattr("app.services.audit.record_audit_event", fake_audit)

    result = _run(goals_api.freeze_benchmark("bench-1", pool=fake_pool, scope=AccessScope.unrestricted(), principal=PRINCIPAL))
    assert result == {"id": "bench-1", "status": "frozen"}
    assert audited.get("actor_subject") == PRINCIPAL.subject
    assert audited.get("action") == "benchmark_frozen"


# ---------------------------------------------------------------------------
# B2 -- report_execution passes execution_verified=False (pure call-shape
# check; the real trust-classification behavior is proven end-to-end in
# the e2e suite against a real record_execution_outcome/evidence_trust).
# ---------------------------------------------------------------------------

def test_B2_report_execution_calls_record_execution_outcome_unverified(monkeypatch):
    import app.mcp_server.server as srv

    captured = {}

    async def fake_record(pool, **kw):
        captured.update(kw)
        return {"verification_state": "candidate", "availability": "active", "verification_stats": {}}

    async def fake_resolve(pool, procedure_id, scope):
        return {"id": procedure_id}

    monkeypatch.setattr("app.services.procedures.record_execution_outcome", fake_record)
    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.unrestricted())

    class FakeCtx:
        request_context = type("RC", (), {"lifespan_context": {"pool": object()}})()

    _run(srv.report_execution(procedure_id="proc-1", success=True, context_key="ctx-a", ctx=FakeCtx()))
    assert captured.get("execution_verified") is False, "report_execution must never report a self-report as execution_verified"
