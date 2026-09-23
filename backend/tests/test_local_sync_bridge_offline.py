"""
DB-free coverage for the local project sync bridge
(app.mcp_server.local_sync_bridge): Origin/Host validation, capability
token lifecycle (single-use, step-scoped, expiring), and the
registry-only project listing. Uses a real Starlette app + TestClient
(real HTTP request/response shapes, not hand-built Request objects) so
header handling is exercised exactly as it would be over real HTTP.

Does not touch app.mcp_server.server (which requires STEALTHLAB_MCP_TOKEN
and a real MCP server construction) -- these tests exercise
local_sync_bridge's handler functions directly, wired into a minimal
Starlette app, matching the same call shape server.py uses.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp_server import local_sync_bridge as bridge

PORT = 8765
ALLOWED_ORIGIN = "https://app.example.test"


def _settings(allowed_origins: str = ALLOWED_ORIGIN) -> SimpleNamespace:
    return SimpleNamespace(sync_bridge_allowed_origins=allowed_origins)


def _app(settings) -> Starlette:
    async def discover(request):
        return await bridge.handle_discover(request, port=PORT)

    async def start(request):
        return await bridge.handle_start_handshake(request, settings=settings, port=PORT)

    async def list_projects(request):
        return await bridge.handle_list_projects(request, settings=settings, port=PORT)

    async def prepare(request):
        return await bridge.handle_prepare_payload(request, settings=settings, port=PORT)

    async def register(request):
        return await bridge.handle_register_local_key(request, settings=settings, port=PORT)

    return Starlette(routes=[
        Route("/.well-known/stealthlab-local", discover, methods=["GET"]),
        Route("/local-sync/start-handshake", start, methods=["POST"]),
        Route("/local-sync/list-projects", list_projects, methods=["POST"]),
        Route("/local-sync/prepare-payload", prepare, methods=["POST"]),
        Route("/local-sync/register-local-key", register, methods=["POST"]),
    ])


@pytest.fixture(autouse=True)
def _clear_capabilities():
    bridge._CAPABILITIES.clear()
    yield
    bridge._CAPABILITIES.clear()


def _client(allowed_origins: str = ALLOWED_ORIGIN) -> TestClient:
    return TestClient(_app(_settings(allowed_origins)))


def _headers(origin=ALLOWED_ORIGIN, host=f"127.0.0.1:{PORT}"):
    h = {}
    if origin is not None:
        h["origin"] = origin
    if host is not None:
        h["host"] = host
    return h


# ------------------------------------------------------------- discovery


def test_discover_reveals_only_service_marker_no_sensitive_data():
    client = _client()
    resp = client.get("/.well-known/stealthlab-local")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"service": "stealthlab-local-bridge", "version": 1}


# --------------------------------------------------------- origin / host


def test_disallowed_origin_is_rejected():
    client = _client()
    resp = client.post(
        "/local-sync/start-handshake", json={"supabase_access_token": "x"},
        headers=_headers(origin="https://evil.example"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "origin_not_allowed"


def test_missing_origin_is_rejected():
    client = _client()
    resp = client.post(
        "/local-sync/start-handshake", json={"supabase_access_token": "x"},
        headers=_headers(origin=None),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "origin_not_allowed"


def test_no_allowed_origins_configured_rejects_everything():
    client = _client(allowed_origins="")
    resp = client.post(
        "/local-sync/start-handshake", json={"supabase_access_token": "x"},
        headers=_headers(),
    )
    assert resp.status_code == 403


def test_forged_host_header_is_rejected_dns_rebinding_defense():
    """Simulates DNS rebinding: an allowed-looking Origin (the attacker
    can freely set this since it's their own page) combined with a Host
    header that reveals the browser actually resolved a different domain
    to 127.0.0.1. Origin alone must not be sufficient."""
    client = _client()
    resp = client.post(
        "/local-sync/start-handshake", json={"supabase_access_token": "x"},
        headers=_headers(origin=ALLOWED_ORIGIN, host="attacker.example:8765"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "host_not_allowed"


def test_correct_origin_and_host_but_bad_token_is_401_not_403():
    """Confirms the checks run in the documented order: transport identity
    first (passes here), THEN the token itself is evaluated."""
    client = _client()
    resp = client.post(
        "/local-sync/start-handshake", json={"supabase_access_token": "not-a-real-jwt"},
        headers=_headers(),
    )
    # No OIDC configured in this offline test -> 503, proving Origin/Host
    # passed and execution reached token verification, not blocked earlier.
    assert resp.status_code in (401, 503)


# ------------------------------------------------------- capability chain


def test_list_projects_rejects_missing_capability():
    client = _client()
    resp = client.post("/local-sync/list-projects", json={}, headers=_headers())
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_or_expired_capability"


def test_capability_is_single_use():
    owner = "user-abc"
    token = bridge._mint_capability(owner, "list")
    client = _client()

    first = client.post("/local-sync/list-projects", json={}, headers={**_headers(), "x-sync-capability": token})
    assert first.status_code == 200

    second = client.post("/local-sync/list-projects", json={}, headers={**_headers(), "x-sync-capability": token})
    assert second.status_code == 401


def test_capability_is_step_scoped():
    owner = "user-abc"
    list_token = bridge._mint_capability(owner, "list")
    client = _client()

    # a "list" capability must not work on the "prepare" route
    resp = client.post(
        "/local-sync/prepare-payload", json={"project_ids": ["x"]},
        headers={**_headers(), "x-sync-capability": list_token},
    )
    assert resp.status_code == 401


def test_expired_capability_is_rejected():
    owner = "user-abc"
    token = bridge._mint_capability(owner, "list")
    bridge._CAPABILITIES[token].expires_at -= 1000  # force expiry without sleeping
    client = _client()

    resp = client.post("/local-sync/list-projects", json={}, headers={**_headers(), "x-sync-capability": token})
    assert resp.status_code == 401


def test_list_projects_returns_registry_contents_and_chains_next_capability(tmp_path, monkeypatch):
    from app.stealth import local_registry

    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    local_registry.upsert_local_project(str(tmp_path / "repo-a"), stable_project_id="pid-a")

    owner = "user-abc"
    token = bridge._mint_capability(owner, "list")
    client = _client()

    resp = client.post("/local-sync/list-projects", json={}, headers={**_headers(), "x-sync-capability": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"][0]["project_id"] == "pid-a"
    assert body["projects"][0]["display_hint"] == "repo-a"
    assert "capability" in body and body["capability"]

    # the chained capability is for the NEXT step ("prepare"), not reusable for "list" again
    assert bridge._CAPABILITIES[body["capability"]].step == "prepare"


def test_prepare_payload_only_returns_allowed_files_for_explicitly_requested_projects(tmp_path, monkeypatch):
    from app.stealth import local_registry

    repo_dir = tmp_path / "repo-a"
    stealth_dir = repo_dir / ".stealth"
    stealth_dir.mkdir(parents=True)
    (stealth_dir / "goals.md").write_text("GOAL|g1|...")
    (repo_dir / "secrets.env").write_text("API_KEY=do-not-upload")

    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    local_registry.upsert_local_project(str(repo_dir), stable_project_id="pid-a")
    local_registry.upsert_local_project(str(tmp_path / "repo-b-never-requested"), stable_project_id="pid-b")

    class _FakePool:
        async def fetch(self, sql, *params):
            return []  # no ledger activity fixture needed for this test

    monkeypatch.setattr(bridge, "_pool", lambda: _FakePool())

    owner = "user-abc"
    token = bridge._mint_capability(owner, "prepare")
    client = _client()

    resp = client.post(
        "/local-sync/prepare-payload", json={"project_ids": ["pid-a"]},
        headers={**_headers(), "x-sync-capability": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert list(body["payloads"].keys()) == ["pid-a"]
    assert body["payloads"]["pid-a"]["files"] == {"goals.md": "GOAL|g1|..."}
    assert body["payloads"]["pid-a"]["activity"] == []
    assert "secrets.env" not in json.dumps(body)
    assert "pid-b" not in body["payloads"]  # never requested, never returned, even though it's in the registry


def test_register_local_key_stores_via_local_key_store(monkeypatch):
    calls = []

    def fake_store_p_dek(pid, val):
        calls.append(("p_dek", pid, val))

    def fake_store_device_token(pid, val):
        calls.append(("device_token", pid, val))

    monkeypatch.setattr("app.stealth.local_key_store.store_p_dek", fake_store_p_dek)
    monkeypatch.setattr("app.stealth.local_key_store.store_device_token", fake_store_device_token)

    owner = "user-abc"
    token = bridge._mint_capability(owner, "register")
    client = _client()

    resp = client.post(
        "/local-sync/register-local-key",
        json={"projects": [{"project_id": "pid-a", "p_dek_base64": "AAAA", "sync_device_token": "tok"}]},
        headers={**_headers(), "x-sync-capability": token},
    )
    assert resp.status_code == 200
    assert resp.json()["results"]["pid-a"] == "cached"
    assert ("p_dek", "pid-a", "AAAA") in calls
    assert ("device_token", "pid-a", "tok") in calls
