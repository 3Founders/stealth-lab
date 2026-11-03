"""OAuth 2.1 for remote tool servers (app/tool_routing/oauth_client.py, docs/tool_gateway_design.md phase 2).

Offline: a fake authorization server over httpx.MockTransport and a fake pool that answers the three OAuth tables by SQL text.
Covers the discovery chain (401 challenge -> protected resource metadata -> authorization server metadata), dynamic client
registration, PKCE, the code exchange, sealing, and the token a later call reads.
"""
from __future__ import annotations

import asyncio
import base64
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.tool_routing import oauth_client as oc

KEY = base64.b64encode(b"k" * 32).decode()
SERVER = "app.example/search"


def run(coro):
    return asyncio.run(coro)


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Conn:
    def __init__(self, pool):
        self.pool = pool

    async def execute(self, sql, *args):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO tool_oauth_clients"):
            self.pool.clients[args[0]] = {"client_id": args[1], "token_endpoint": args[3]}
        elif s.startswith("INSERT INTO tool_oauth_pending"):
            self.pool.pending[args[0]] = {"owner": args[1], "server": args[2], "client_id": args[3], "verifier": args[4],
                                          "redirect_uri": args[5], "token_endpoint": args[6], "resource": args[7], "used": False}
        elif s.startswith("INSERT INTO tool_oauth_tokens"):
            self.pool.tokens[(args[0], args[1])] = {"access_token": args[2], "refresh_token": args[3],
                                                    "token_endpoint": args[4], "client_id": args[5], "expires_at": args[7]}
        elif s.startswith("DELETE FROM tool_oauth_tokens"):
            self.pool.tokens.pop((args[0], args[1]), None)
        return "OK"

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split())
        if s.startswith("SELECT client_id, token_endpoint FROM tool_oauth_clients"):
            return self.pool.clients.get(args[0])
        if s.startswith("UPDATE tool_oauth_pending SET used_at = now()"):
            row = self.pool.pending.get(args[0])
            if not row or row["used"]:
                return None
            row["used"] = True
            return {"owner_id": row["owner"], "server": row["server"], "client_id": row["client_id"],
                    "verifier": row["verifier"], "redirect_uri": row["redirect_uri"],
                    "token_endpoint": row["token_endpoint"], "resource": row["resource"]}
        if s.startswith("SELECT access_token, refresh_token, token_endpoint, client_id, expires_at"):
            return self.pool.tokens.get((args[0], args[1]))
        return None

    async def fetchval(self, sql, *args):
        return 1 if (args[0], args[1]) in self.pool.tokens else None

    def transaction(self):
        return Tx()


class Acq:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return Conn(self.pool)

    async def __aexit__(self, *exc):
        return False


class Pool:
    def __init__(self):
        self.pending, self.clients, self.tokens = {}, {}, {}

    def acquire(self):
        return Acq(self)


def auth_server(*, methods=("S256",)):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json={"resource": "https://mcp.example/mcp",
                                             "authorization_servers": ["https://auth.example"],
                                             "scopes_supported": ["mcp:tools"]})
        if url.endswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json={"issuer": "https://auth.example",
                                             "authorization_endpoint": "https://auth.example/authorize",
                                             "token_endpoint": "https://auth.example/token",
                                             "registration_endpoint": "https://auth.example/register",
                                             "code_challenge_methods_supported": list(methods),
                                             "scopes_supported": ["mcp:tools"]})
        if url.endswith("/register"):
            return httpx.Response(201, json={"client_id": "cid-1"})
        if url.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                                             "scope": "mcp:tools"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "org_key_encryption_key", KEY, raising=False)
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")


def test_parse_challenge_reads_the_resource_metadata_and_scope():
    ch = oc.parse_challenge('Bearer realm="mcp", resource_metadata="https://mcp.example/.well-known/oauth-protected-resource", scope="mcp:tools"')
    assert ch["resource_metadata"].endswith("/.well-known/oauth-protected-resource")
    assert ch["scope"] == "mcp:tools"


def test_discovery_follows_the_spec_and_requires_s256():
    async def go():
        async with httpx.AsyncClient(transport=auth_server()) as client:
            return await oc.discover("https://mcp.example/mcp", {"resource_metadata": "https://mcp.example/.well-known/oauth-protected-resource"}, client=client)
    meta = run(go())
    assert meta["authorization_endpoint"] == "https://auth.example/authorize"
    assert meta["token_endpoint"] == "https://auth.example/token"
    assert meta["registration_endpoint"] == "https://auth.example/register"
    assert meta["scope"] == "mcp:tools"

    async def plain_only():
        async with httpx.AsyncClient(transport=auth_server(methods=("plain",))) as client:
            with pytest.raises(oc.OAuthRefused):
                await oc.discover("https://mcp.example/mcp", {"resource_metadata": "https://mcp.example/.well-known/oauth-protected-resource"}, client=client)
    run(plain_only())


def test_begin_registers_with_pkce_and_complete_stores_a_sealed_token():
    pool = Pool()

    async def flow():
        async with httpx.AsyncClient(transport=auth_server()) as client:
            link = await oc.begin(pool, "user-1", SERVER, "https://mcp.example/mcp", "https://kell.example",
                                  {"resource_metadata": "https://mcp.example/.well-known/oauth-protected-resource"},
                                  client=client, check_host=False)
            q = parse_qs(urlsplit(link).query)
            assert q["client_id"] == ["cid-1"]
            assert q["code_challenge_method"] == ["S256"]
            assert q["redirect_uri"] == ["https://kell.example/tools/connect/callback"]
            assert q["scope"] == ["mcp:tools"]
            assert "code_challenge" in q and q["state"]
            owner, server = await oc.complete(pool, q["state"][0], "the-code", client=client)
            assert (owner, server) == ("user-1", SERVER)
            return await oc.token(pool, "user-1", SERVER, client=client)

    assert run(flow()) == "at-1"
    sealed = pool.tokens[("user-1", SERVER)]["access_token"]
    assert sealed != "at-1" and sealed.startswith("v1.")          # secret_box, not plaintext
    assert run(oc.has_token(pool, "user-1", SERVER)) is True
    run(oc.forget(pool, "user-1", SERVER))
    assert run(oc.has_token(pool, "user-1", SERVER)) is False


def test_a_replayed_state_is_refused():
    pool = Pool()

    async def flow():
        async with httpx.AsyncClient(transport=auth_server()) as client:
            link = await oc.begin(pool, "user-1", SERVER, "https://mcp.example/mcp", "https://kell.example",
                                  {"resource_metadata": "https://mcp.example/.well-known/oauth-protected-resource"},
                                  client=client, check_host=False)
            state = parse_qs(urlsplit(link).query)["state"][0]
            await oc.complete(pool, state, "the-code", client=client)
            with pytest.raises(oc.OAuthRefused):
                await oc.complete(pool, state, "the-code", client=client)

    run(flow())
