"""
DB-free coverage for require_admin_api_key (app/api/deps.py) and its
wiring onto the real /v1/admin/* router (app/api/admin.py) -- exercises
ACTUAL FastAPI dependency resolution via httpx.ASGITransport against the
real app.main.app object (no lifespan run, same offline convention
test_mcp_root_route_offline.py already established), not a direct
Python function call. This is the layer a direct-call e2e test cannot
prove: that the auth gate is really wired into HTTP dispatch, not just
defined somewhere.

Uses the `X-Admin-Api-Key` header, NOT `Authorization: Bearer` -- a real
bug found while verifying this live: `actor_middleware` (app/services/
authn.py) intercepts the `authorization` header globally and, whenever
real OIDC/Supabase config is present, tries to validate it as a JWT,
401-ing anything else BEFORE this dependency or routing ever runs. This
offline suite's own pytest-triggered TEST environment does not reproduce
that collision (confirmed: it did not catch the bug -- only a real
`uvicorn` boot with this checkout's real `.env` did), which is exactly
why the real-HTTP-boot rehearsal in the session transcript, not just
these tests, is what actually caught it.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import app.main as main_module
from app.api.admin import get_pool
from app.api.deps import require_admin_api_key


class _FakeConn:
    async def fetch(self, *a, **kw):
        return []

    async def fetchval(self, *a, **kw):
        return 0


class _FakeAcquireCM:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """Just enough surface for GET /v1/admin/index-lag's own
    get_index_lag() to complete without a real database -- this test's
    only goal is proving the auth gate passed, not exercising index-lag's
    own real query logic (already covered elsewhere)."""

    def acquire(self):
        return _FakeAcquireCM()


def _run(coro):
    return asyncio.run(coro)


def test_require_admin_api_key_fails_closed_when_unset(monkeypatch):
    """The real default posture: no ADMIN_API_KEY configured means every
    request is refused, never silently allowed."""
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", None)
    with pytest.raises(Exception) as excinfo:
        _run(require_admin_api_key(x_admin_api_key="anything"))
    assert getattr(excinfo.value, "status_code", None) == 401


def test_require_admin_api_key_rejects_missing_header(monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")
    with pytest.raises(Exception) as excinfo:
        _run(require_admin_api_key(x_admin_api_key=None))
    assert getattr(excinfo.value, "status_code", None) == 401


def test_require_admin_api_key_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")
    with pytest.raises(Exception) as excinfo:
        _run(require_admin_api_key(x_admin_api_key="wrong-key"))
    assert getattr(excinfo.value, "status_code", None) == 401


def test_require_admin_api_key_accepts_the_correct_key(monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")
    # Must not raise.
    _run(require_admin_api_key(x_admin_api_key="real-key"))


# --- real HTTP dispatch against the actual app.main.app router ---

def test_admin_route_returns_401_over_real_http_with_no_key(monkeypatch):
    """The layer a direct function call can never prove: a real request
    through app.main's actual FastAPI router, with no credentials at all,
    must be refused before the endpoint body ever runs."""
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")

    async def _run_request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            resp = await client.get("/v1/admin/index-lag")
            assert resp.status_code == 401

    _run(_run_request())


def test_admin_route_returns_401_over_real_http_with_wrong_key(monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")

    async def _run_request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            resp = await client.get(
                "/v1/admin/index-lag", headers={"X-Admin-Api-Key": "wrong-key"},
            )
            assert resp.status_code == 401

    _run(_run_request())


def test_admin_route_ignores_a_real_authorization_bearer_header():
    """The specific real bug this session found: X-Admin-Api-Key must be
    the ONLY thing this dependency reads. A caller sending a plausible-
    looking Authorization: Bearer header (e.g. an OIDC token, or -- the
    real mistake made here -- the admin key itself in the wrong header)
    must still get refused by require_admin_api_key, not accidentally
    accepted via some other path."""
    import app.api.deps as deps_module

    async def _run_request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            resp = await client.get(
                "/v1/admin/index-lag", headers={"Authorization": "Bearer real-key"},
            )
            assert resp.status_code == 401

    orig = deps_module.settings.admin_api_key
    deps_module.settings.admin_api_key = "real-key"
    try:
        _run(_run_request())
    finally:
        deps_module.settings.admin_api_key = orig


def test_admin_route_passes_auth_gate_over_real_http_with_correct_key(monkeypatch):
    """With the right key in X-Admin-Api-Key, the request must get PAST
    the auth dependency -- proven by it NOT being a 401. (It may still
    fail further in because app.state.pool is never bound without
    running the real lifespan in this offline test -- that failure mode,
    if any, would be a 500, never a 401 masquerading as "auth worked".
    This test's whole point is isolating the auth gate itself.)"""
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "real-key")
    main_module.app.dependency_overrides[get_pool] = lambda: _FakePool()

    async def _run_request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            resp = await client.get(
                "/v1/admin/index-lag", headers={"X-Admin-Api-Key": "real-key"},
            )
            assert resp.status_code == 200, "the correct key must pass the auth gate and reach the real endpoint"
            assert resp.json()["total_stale"] == 0

    try:
        _run(_run_request())
    finally:
        main_module.app.dependency_overrides.pop(get_pool, None)
