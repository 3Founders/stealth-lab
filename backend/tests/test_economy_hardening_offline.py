"""
Offline (no real DB, no real network) tests for the economy hardening pass
(migration 102 audit -> this hardening pass). Same direct-call/dependency-
override convention test_goals_api_offline.py already established:
`app.dependency_overrides` for the identity/rate-limit seams, real FastAPI
routing via `httpx.ASGITransport` where the thing under test IS the
request-body-stripping behavior (pydantic's `extra='ignore'` running for
real), and a monkeypatched service layer that just records what identity
it was called with.

Covers hardening-spec §14 invariants A, B, C, D at the API boundary (no DB
needed -- these are pure "does the router trust the client" questions).
E, F, G, H, I, J, K, L need a real Postgres (execution_runs/evidence/
credit_ledger_events rows, concurrency) and live in
tests/test_economy_hardening_e2e.py, gated on DATABASE_URL like every
other *_e2e.py file in this suite.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

import app.api.economy as economy_api
from app.api.deps import AuthenticatedPrincipal, require_authenticated_user

ALICE = AuthenticatedPrincipal(user_id="user-alice", subject="sub-alice", email="alice@example.com")
BOB = AuthenticatedPrincipal(user_id="user-bob", subject="sub-bob", email="bob@example.com")
ADMIN = AuthenticatedPrincipal(user_id="user-admin", subject="sub-admin", scopes=frozenset({"admin:ops"}))


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Schema shape: identity fields must not exist on the request bodies at all
# ---------------------------------------------------------------------------

def test_request_bodies_carry_no_identity_fields():
    banned = {"submitted_by", "executed_by", "reviewed_by", "created_by", "contributor_id",
              "reviewer_id", "verifier_id", "beneficiary_id", "account_id", "owner_id"}
    for model in (
        economy_api.ProcedureSubmissionIn, economy_api.BenchmarkSubmissionIn,
        economy_api.SubmissionReviewIn, economy_api.UsageEventIn, economy_api.ClawbackIn,
    ):
        present = banned & set(model.model_fields.keys())
        assert not present, f"{model.__name__} still exposes client-settable identity field(s): {present}"


def test_usage_event_in_carries_no_outcome_or_layer_fields():
    """outcome_state / verification_layer must be server-derived, not client input (§4/§7)."""
    fields = set(economy_api.UsageEventIn.model_fields.keys())
    assert "outcome_state" not in fields
    assert "verification_layer" not in fields
    assert "is_self_use" not in fields


# ---------------------------------------------------------------------------
# A: an authenticated user cannot submit as another identity
# ---------------------------------------------------------------------------

def test_A_procedure_submission_ignores_client_supplied_identity(monkeypatch):
    import app.main as main_module

    captured = {}

    async def fake_create(pool, *, actor_subject, **kw):
        captured["actor_subject"] = actor_subject
        captured["kw"] = kw
        return {"id": "sub-1", "actor_subject": actor_subject}

    monkeypatch.setattr(economy_api.submissions_service, "create_procedure_submission", fake_create)
    main_module.app.dependency_overrides[economy_api._authenticated_and_rate_limited] = lambda: ALICE
    main_module.app.dependency_overrides[economy_api.get_pool] = lambda: object()
    try:
        async def _call():
            transport = httpx.ASGITransport(app=main_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # A client attempting to impersonate "mallory" via an extra body
                # field it hopes the server still trusts.
                return await client.post(
                    "/v1/economy/procedure-submissions",
                    json={
                        "goal_id": "goal-1", "submission_type": "new", "name": "do the thing",
                        "steps": ["step one"], "submitted_by": "mallory", "owner_id": "mallory",
                    },
                )
        resp = _run(_call())
    finally:
        main_module.app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert captured["actor_subject"] == ALICE.subject
    assert "submitted_by" not in captured["kw"]
    assert "owner_id" not in captured["kw"]


# ---------------------------------------------------------------------------
# B: an authenticated user cannot execute as another identity
# ---------------------------------------------------------------------------

def test_B_usage_event_ignores_client_supplied_executor(monkeypatch):
    import app.main as main_module

    captured = {}

    async def fake_record(pool, *, executor_subject, **kw):
        captured["executor_subject"] = executor_subject
        captured["kw"] = kw
        return {"id": "usage-1", "outcome_state": "unknown", "is_self_use": False, "executed_by": executor_subject}

    monkeypatch.setattr(economy_api.verification_service, "record_usage_event", fake_record)
    main_module.app.dependency_overrides[economy_api._authenticated_and_rate_limited] = lambda: BOB
    main_module.app.dependency_overrides[economy_api.get_pool] = lambda: object()
    try:
        async def _call():
            transport = httpx.ASGITransport(app=main_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/v1/economy/usage-events",
                    json={"procedure_row_id": "proc-1", "executed_by": "someone-else"},
                )
        resp = _run(_call())
    finally:
        main_module.app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert captured["executor_subject"] == BOB.subject
    assert "executed_by" not in captured["kw"]
    assert "outcome_state" not in captured["kw"]
    assert "verification_layer" not in captured["kw"]


# ---------------------------------------------------------------------------
# C: a reviewer cannot spoof the audit identity
# ---------------------------------------------------------------------------

def test_C_review_ignores_client_supplied_reviewer(monkeypatch):
    import app.main as main_module

    captured = {}

    async def fake_review(pool, *, submission_id, decision, actor_subject, note=None):
        captured["actor_subject"] = actor_subject
        return {"id": submission_id, "status": decision, "reviewed_by": actor_subject}

    monkeypatch.setattr(economy_api.submissions_service, "review_procedure_submission", fake_review)
    main_module.app.dependency_overrides[require_authenticated_user] = lambda: ADMIN
    main_module.app.dependency_overrides[economy_api.get_pool] = lambda: object()
    try:
        async def _call():
            transport = httpx.ASGITransport(app=main_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/v1/economy/procedure-submissions/sub-1/review",
                    json={"decision": "accepted", "reviewed_by": "not-the-real-reviewer"},
                )
        resp = _run(_call())
    finally:
        main_module.app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert captured["actor_subject"] == ADMIN.subject


# ---------------------------------------------------------------------------
# D: only the contributor themselves (or an ADMIN_OPS holder) may read
# their Credits/Standing -- verified directly against the pure
# authorization helper (no DB, no HTTP needed for this one).
# ---------------------------------------------------------------------------

def test_D_require_self_or_admin_allows_self():
    economy_api._require_self_or_admin(ALICE, ALICE.subject)  # must not raise


def test_D_require_self_or_admin_allows_admin_for_others():
    economy_api._require_self_or_admin(ADMIN, ALICE.subject)  # must not raise


def test_D_require_self_or_admin_rejects_other_users():
    with pytest.raises(HTTPException) as exc_info:
        economy_api._require_self_or_admin(BOB, ALICE.subject)
    assert exc_info.value.status_code == 403


def test_D_credits_endpoints_reject_reading_someone_elses_data(monkeypatch):
    """End-to-end through real routing: Bob authenticated, asking for
    Alice's balance, must be refused -- never reach the service layer."""
    import app.main as main_module

    called = {"balance": False}

    async def fake_get_balance(pool, contributor_id):
        called["balance"] = True
        return 999.0

    monkeypatch.setattr(economy_api.credits_service, "get_balance", fake_get_balance)
    main_module.app.dependency_overrides[require_authenticated_user] = lambda: BOB
    main_module.app.dependency_overrides[economy_api.get_pool] = lambda: object()
    try:
        async def _call():
            transport = httpx.ASGITransport(app=main_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(f"/v1/economy/contributors/{ALICE.subject}/credits")
        resp = _run(_call())
    finally:
        main_module.app.dependency_overrides.clear()

    assert resp.status_code == 403, resp.text
    assert called["balance"] is False


def test_D_credits_endpoints_unauthenticated_is_rejected():
    """No dependency override for require_authenticated_user at all -- the
    real dependency runs, and with no bearer token / OIDC configured in
    this offline process it must deny, never fall through to a public read.
    `get_pool` is stubbed only so dependency resolution doesn't fail on the
    unrelated fact that no app lifespan (and therefore no real pool) is
    running in this offline test -- it must never be reached if auth is
    doing its job."""
    import app.main as main_module

    main_module.app.dependency_overrides[economy_api.get_pool] = lambda: object()
    try:
        async def _call():
            transport = httpx.ASGITransport(app=main_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(f"/v1/economy/contributors/{ALICE.subject}/credits")
        resp = _run(_call())
    finally:
        main_module.app.dependency_overrides.clear()
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# Pure-function coverage: Layer 1/2 decisions and the ledger conflict-clause
# builder (no DB needed -- exercised directly).
# ---------------------------------------------------------------------------

def test_layer1_flags_missing_fields():
    from app.economy.verification import evaluate_layer1

    ok = evaluate_layer1(name="do it", steps=["a step"], rationale="because")
    assert ok["passed"] is True

    bad = evaluate_layer1(name="", steps=[], rationale=None)
    assert bad["passed"] is False
    assert set(bad["issues"]) == {"missing_name", "missing_steps", "missing_rationale"}


def test_layer2_never_returns_accepted():
    from app.economy.verification import evaluate_layer2

    async def _run_it():
        for score in (None, 0.5, 0.9, 0.99):
            result = await evaluate_layer2(
                submission={"name": "x"}, duplicate={"score": score, "best_match_kind": "procedure"},
                layer1={"passed": True, "issues": []},
            )
            assert result["decision"] in ("reject", "candidate", "needs_review")
            assert result["decision"] != "accepted"

    _run(_run_it())


def test_credit_ledger_conflict_clause_matches_migration_103_indexes():
    from app.economy.credits import _conflict_clause

    submission_clause = _conflict_clause(submission_id="sub-1", usage_event_id=None)
    assert "submission_id, reason" in submission_clause
    assert "WHERE submission_id IS NOT NULL" in submission_clause

    usage_clause = _conflict_clause(submission_id=None, usage_event_id="usage-1")
    assert "usage_event_id, reason, contributor_id" in usage_clause
    assert "WHERE usage_event_id IS NOT NULL" in usage_clause

    assert _conflict_clause(submission_id=None, usage_event_id=None) == ""
