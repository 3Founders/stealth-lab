"""
Offline tests for remote MCP sign-in (app/mcp_server/oauth_resource.py): the
401 challenge, the RFC 9728 document naming Supabase as the authorization
server, the scopes it advertises, when anonymous reads are on, and the
server's own wiring. The flow test drives a real SDK app in-process (httpx
ASGI transport); no network, no DB, no Supabase.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from app.mcp_server import oauth_resource as oa

SUPA = SimpleNamespace(supabase_project_url="https://abc.supabase.co/", supabase_jwt_audience="authenticated")
NONE = SimpleNamespace(supabase_project_url=None, supabase_jwt_audience=None)
ORIGIN = "https://mcp.example.com"


def _run(coro):
    return asyncio.run(coro)


def test_supabase_is_the_authorization_server_only_when_the_preset_is_complete():
    assert oa.supabase_authorization_server(SUPA) == "https://abc.supabase.co/auth/v1"
    assert oa.supabase_authorization_server(NONE) is None
    half = SimpleNamespace(supabase_project_url="https://abc.supabase.co", supabase_jwt_audience=None)
    assert oa.supabase_authorization_server(half) is None
    assert oa.authorization_server_url(SUPA, ORIGIN) == "https://abc.supabase.co/auth/v1"
    assert oa.authorization_server_url(NONE, ORIGIN) == ORIGIN


def test_metadata_advertises_only_scopes_supabase_can_issue():
    doc = oa.protected_resource_metadata("https://abc.supabase.co/auth/v1", ORIGIN)
    assert doc["resource"] == f"{ORIGIN}/mcp"
    assert doc["authorization_servers"] == ["https://abc.supabase.co/auth/v1"]
    assert set(doc["scopes_supported"]) <= {"openid", "email", "profile", "phone"}
    assert "stealthlab:tools" not in doc["scopes_supported"]


def test_challenge_points_at_the_same_document_the_sdk_names():
    sdk = str(build_resource_metadata_url(AnyHttpUrl(f"{ORIGIN}/mcp")))
    assert oa.resource_metadata_url(ORIGIN) == sdk
    assert oa.www_authenticate(ORIGIN, error="invalid_token") == (
        f'Bearer error="invalid_token", resource_metadata="{sdk}"')


def test_anonymous_reads_default_off_only_for_a_hosted_supabase_server():
    assert oa.anonymous_reads_enabled({}, public_origin=ORIGIN, supabase_configured=True) is False
    assert oa.anonymous_reads_enabled({}, public_origin=None, supabase_configured=True) is True
    assert oa.anonymous_reads_enabled({}, public_origin=ORIGIN, supabase_configured=False) is True
    on = {"STEALTHLAB_MCP_ANONYMOUS_READS": "1"}
    off = {"STEALTHLAB_MCP_ANONYMOUS_READS": "0"}
    assert oa.anonymous_reads_enabled(on, public_origin=ORIGIN, supabase_configured=True) is True
    assert oa.anonymous_reads_enabled(off, public_origin=None, supabase_configured=False) is False


# ------------------------------------------------------------------ the flow, against a real SDK app
class _Verifier(TokenVerifier):
    async def verify_token(self, token):
        if token == "supabase-user-token":
            return AccessToken(token=token, client_id="u1", scopes=["stealthlab:tools"], subject="u1")
        return None


def _hosted_app(env):
    mcp = MCPServer(name="t", token_verifier=_Verifier(), auth=AuthSettings(
        issuer_url=AnyHttpUrl(oa.authorization_server_url(SUPA, ORIGIN)),
        resource_server_url=AnyHttpUrl(oa.resource_url(ORIGIN)), required_scopes=["stealthlab:tools"]))
    app = oa.wrap_app(mcp.streamable_http_app(stateless_http=True, json_response=True), SUPA, server_origin=ORIGIN,
                      public_origin=ORIGIN, env=env)
    return app, mcp


async def _call(app, method, path, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as c:
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"} if method == "POST" else None
        return await c.request(method, path, json=body, headers={
            "accept": "application/json, text/event-stream", **(headers or {})})


def test_hosted_flow_challenges_then_discovers_supabase():
    app, mcp = _hosted_app({})

    async def flow():
        async with mcp.session_manager.run():
            # 1. No token -> 401 pointing at the resource metadata.
            r = await _call(app, "POST", "/mcp")
            assert r.status_code == 401
            assert f'resource_metadata="{oa.resource_metadata_url(ORIGIN)}"' in r.headers["www-authenticate"]
            # 2. The document names Supabase and only scopes it can issue.
            doc = (await _call(app, "GET", "/.well-known/oauth-protected-resource/mcp")).json()
            assert doc["authorization_servers"] == ["https://abc.supabase.co/auth/v1"]
            assert "stealthlab:tools" not in doc["scopes_supported"]
            root = await _call(app, "GET", "/.well-known/oauth-protected-resource")
            assert root.status_code == 200 and root.headers["access-control-allow-origin"] == "*"
            assert (await _call(app, "OPTIONS", "/.well-known/oauth-protected-resource/mcp")).status_code == 204
            # 3. A garbage token is still 401; a validated one passes the SDK's scope gate and is served.
            assert (await _call(app, "POST", "/mcp", {"authorization": "Bearer nope"})).status_code == 401
            ok = await _call(app, "POST", "/mcp", {"authorization": "Bearer supabase-user-token"})
            assert ok.status_code == 200, ok.text

    _run(flow())


def test_anonymous_reads_override_serves_token_less_callers_without_a_challenge():
    app, _ = _hosted_app({"STEALTHLAB_MCP_ANONYMOUS_READS": "1"})
    r = _run(_call(app, "POST", "/mcp"))
    # The injector supplies the anonymous-read token, which this stub verifier doesn't know -> 401;
    # what matters is that the injected credential reached the verifier, i.e. the injector is on.
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]


def test_without_supabase_the_sdk_route_and_behaviour_are_unchanged():
    from app.mcp_server.anonymous_read import AnonymousReadInjectorMiddleware

    inner = object()
    wrapped = oa.wrap_app(inner, NONE, server_origin=ORIGIN, public_origin=None, env={})
    assert isinstance(wrapped, AnonymousReadInjectorMiddleware)


# ------------------------------------------------------------------ the real server's wiring
def test_server_route_gate_401_carries_the_resource_metadata():
    import app.mcp_server.server as srv

    class _Req:
        headers = {"x-forwarded-for": "1.2.3.4"}
        client = SimpleNamespace(host="1.2.3.4")

    resp = _run(srv._route_gate(_Req()))
    assert resp.status_code == 401
    assert f'resource_metadata="{oa.resource_metadata_url(srv._ISSUER_URL)}"' in resp.headers["www-authenticate"]


def test_server_keeps_requiring_the_tools_scope_and_names_the_right_issuer():
    import app.mcp_server.server as srv

    auth = srv.server.settings.auth
    assert auth.required_scopes == ["stealthlab:tools"]
    assert str(auth.issuer_url).rstrip("/") == oa.authorization_server_url(srv.settings, srv._ISSUER_URL).rstrip("/")


def test_a_supabase_only_shared_deployment_is_not_refused_for_missing_oidc(monkeypatch):
    import app.mcp_server.server as srv

    monkeypatch.setattr(srv.settings, "deployment_mode", "shared")
    monkeypatch.setattr(srv.settings, "oidc_issuer", None)
    monkeypatch.setattr(srv.settings, "oidc_audience", None)
    monkeypatch.setattr(srv.settings, "supabase_project_url", "https://abc.supabase.co")
    monkeypatch.setattr(srv.settings, "supabase_jwt_audience", "authenticated")
    verifier = srv._build_token_verifier("shared-secret")
    assert verifier._oidc_config.issuer == "https://abc.supabase.co/auth/v1"
    assert verifier._allow_shared_token is False

    monkeypatch.setattr(srv.settings, "supabase_project_url", None)
    monkeypatch.setattr(srv.settings, "supabase_jwt_audience", None)
    import pytest

    with pytest.raises(RuntimeError):
        srv._build_token_verifier("shared-secret")
