"""Live-database tests for the agent contribution path: `GET /v1/goals/choose`
and the MCP `submit_way` tool.

The gating claims made about agent submissions are proved here against real
SQL, not assumed: identity comes from the token, nothing self-promotes, secrets
are redacted, submissions are rate limited, and an unreviewed submission is not
served to other agents as a chosen way.
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.economy import constants as econ
from app.economy import submissions as submissions_service
from app.execution.goal_resolution import resolve_goal
from app.services.access import AccessScope
from app.services.goals import find_or_create_goal

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def srv(monkeypatch):
    os.environ.setdefault("STEALTHLAB_MCP_TOKEN", "test-token")
    import app.mcp_server.server as server_module

    async def no_embedding(_embedder, _text):
        return None

    monkeypatch.setattr(submissions_service, "embed_submission_text", no_embedding)
    return server_module


def _ctx(pool):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))


async def _goal(pool) -> str:
    created = await find_or_create_goal(
        pool, canonical_name=f"e2e submit way {uuid.uuid4().hex[:8]}", scope_type="global",
        provenance="system_pending_review", judge_mode="none", status="active",
    )
    return created["id"]


PRE = json.dumps([{"subject": "repo", "predicate": "has", "value": "a test suite"}])
OUT = json.dumps({"tests": "pass"})


def _steps() -> str:
    return json.dumps([{"order": 1, "goal": "Run the test suite and read the failures"},
                       {"order": 2, "goal": "Fix the failing assertion and re-run until green"}])


@pytest.mark.asyncio
async def test_anonymous_callers_cannot_submit(pool, srv, monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.anonymous())
    out = await srv.submit_way(await _goal(pool), "a way", _steps(), "because", PRE, OUT, _ctx(pool))
    assert out.startswith("REFUSED: sign in")


@pytest.mark.asyncio
async def test_bad_input_is_refused_before_anything_is_written(pool, srv, monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.for_user(f"u-{uuid.uuid4().hex[:6]}"))
    goal = await _goal(pool)
    ctx = _ctx(pool)
    assert "steps_json must be valid JSON" in await srv.submit_way(goal, "n", "not json", "r", PRE, OUT, ctx)
    assert "1-50 steps" in await srv.submit_way(goal, "n", "[]", "r", PRE, OUT, ctx)
    assert "1-50 steps" in await srv.submit_way(goal, "n", json.dumps(["s"] * 51), "r", PRE, OUT, ctx)
    assert "non-empty string" in await srv.submit_way(goal, "n", json.dumps([{"order": 1}]), "r", PRE, OUT, ctx)
    assert "not found or not visible" in await srv.submit_way(str(uuid.uuid4()), "n", _steps(), "r", PRE, OUT, ctx)
    assert "rationale" in await srv.submit_way(goal, "n", _steps(), "   ", PRE, OUT, ctx)
    assert "parent_procedure_row_id" in await srv.submit_way(goal, "n", _steps(), "r", PRE, OUT, ctx, submission_type="improvement")
    assert await pool.fetchval("SELECT count(*) FROM procedure_submissions WHERE goal_id = $1::uuid", goal) == 0


@pytest.mark.asyncio
async def test_submission_is_attributed_to_the_token_and_never_self_promotes(pool, srv, monkeypatch):
    viewer = f"u-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.for_user(viewer))
    goal = await _goal(pool)
    out = json.loads(await srv.submit_way(
        goal, "Fix a failing test", _steps(), "Reading failures first finds the real cause.",
        PRE, OUT, _ctx(pool),
    ))
    assert out["status"] in ("candidate", "needs_review")
    row = await pool.fetchrow("SELECT * FROM procedure_submissions WHERE id = $1::uuid", out["submission_id"])
    assert row["submitted_by"] == viewer and row["reviewed_by"] is None and row["status"] != "accepted"
    proc = await pool.fetchrow(
        "SELECT verification_state, provenance FROM procedures WHERE id = $1::uuid", out["procedure_row_id"])
    assert proc["verification_state"] == "candidate"  # submitting never verifies
    # no credits were awarded for an unreviewed submission
    assert await pool.fetchval(
        "SELECT count(*) FROM credit_ledger_events WHERE contributor_id = $1", viewer) == 0
    # another agent asking for this Goal is not handed the unreviewed way as a chosen procedure
    tree = await resolve_goal(pool, goal, context={}, scope=AccessScope.anonymous(), max_depth=2)
    assert tree.procedure is None


@pytest.mark.asyncio
async def test_secrets_are_redacted_before_storage(pool, srv, monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.for_user(f"u-{uuid.uuid4().hex[:6]}"))
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    out = json.loads(await srv.submit_way(
        await _goal(pool), "Deploy", json.dumps([f"export KEY={secret} then deploy"]), "because", PRE, OUT, _ctx(pool)))
    assert out["redacted"] is True
    stored = await pool.fetchval("SELECT content::text FROM procedure_submissions WHERE id = $1::uuid",
                                 out["submission_id"])
    assert secret not in stored


@pytest.mark.asyncio
async def test_submissions_are_rate_limited_per_user(pool, srv, monkeypatch):
    monkeypatch.setattr(econ, "SUBMISSION_RATE_LIMIT_MAX", 2)
    viewer = f"u-{uuid.uuid4().hex[:6]}"  # one user across calls: the limit is per identity
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.for_user(viewer))
    goal = await _goal(pool)
    ctx = _ctx(pool)
    for i in range(2):
        assert "submission_id" in await srv.submit_way(goal, f"way {i}", _steps(), "because", PRE, OUT, ctx)
    assert "rate limit" in await srv.submit_way(goal, "way 3", _steps(), "because", PRE, OUT, ctx)


@pytest.mark.asyncio
async def test_submit_way_is_on_the_v1_surface_and_needs_write_scope(srv):
    assert "submit_way" in srv.V1_TOOLS
    assert srv._TOOL_SCOPES["submit_way"] == srv._WRITE
