"""
Supabase tokens are split by where they came from (services/authn.py: supabase_token_allowed).

Supabase gives every token aud="authenticated", so audience can't keep one service's tokens off another.
Tokens from the OAuth flow MCP clients use carry the OAuth app's `client_id`; website sessions don't. The MCP
server accepts only the former, the REST API only the latter. Proved with real signed tokens through the real
API middleware and the real MCP token verifier.
"""
from __future__ import annotations

import asyncio
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.authn import OidcConfig, StaticJwks, jwk_from_public_numbers, make_actor_middleware

KID = "kid-split"
SUPA = OidcConfig(issuer="https://p.supabase.co/auth/v1", audience="authenticated",
                  jwks_url="https://p.supabase.co/auth/v1/.well-known/jwks.json", allowed_algs=("ES256", "RS256"))
GENERIC = OidcConfig(issuer="https://idp.example.com", audience="kel", jwks_url="https://idp.example.com/jwks",
                     allowed_algs=("RS256",))


@pytest.fixture(scope="module")
def key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = k.public_key().public_numbers()
    jwks = StaticJwks({KID: jwk_from_public_numbers(
        n.n.to_bytes((n.n.bit_length() + 7) // 8, "big"), n.e.to_bytes((n.e.bit_length() + 7) // 8, "big"), KID)})
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem, jwks


def _tok(pem, cfg, *, client_id=None):
    now = int(time.time())
    claims = {"iss": cfg.issuer, "aud": cfg.audience, "sub": "user-1", "iat": now, "exp": now + 600,
              **({"client_id": client_id} if client_id else {})}
    return pyjwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


def _api_status(token, cfg, jwks, *, split=True):
    seen = {}

    async def app(scope, receive, send):
        seen["through"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = make_actor_middleware(cfg, jwks, private_visibility_enabled=False, split_supabase_tokens=split)(app)
    out = {}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] = msg.get("body", b"")

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "path": "/v1/economy/procedure-submissions", "method": "GET",
             "headers": [(b"authorization", f"Bearer {token}".encode())]}
    asyncio.run(mw(scope, receive, send))
    return out["status"], out.get("body", b"")


def test_the_api_takes_website_sessions_and_refuses_mcp_app_tokens(key):
    pem, jwks = key
    assert _api_status(_tok(pem, SUPA), SUPA, jwks)[0] == 200
    status, body = _api_status(_tok(pem, SUPA, client_id="chatgpt-app"), SUPA, jwks)
    assert status == 401 and b"connected MCP app" in body
    # switch off -> both work; a generic (non-Supabase) IdP is never split
    assert _api_status(_tok(pem, SUPA, client_id="chatgpt-app"), SUPA, jwks, split=False)[0] == 200
    assert _api_status(_tok(pem, GENERIC, client_id="x"), GENERIC, jwks)[0] == 200


def test_the_mcp_server_takes_mcp_app_tokens_and_refuses_website_sessions(key, monkeypatch):
    import app.mcp_server.server as srv

    pem, jwks = key
    verifier = srv.OidcAwareTokenVerifier("", SUPA, jwks, allow_shared_token=False)
    ok = asyncio.run(verifier.verify_token(_tok(pem, SUPA, client_id="opencode-app")))
    assert ok is not None and ok.subject == "user-1"
    assert asyncio.run(verifier.verify_token(_tok(pem, SUPA))) is None          # a website session
    monkeypatch.setattr(srv.settings, "split_supabase_tokens", False)
    assert asyncio.run(verifier.verify_token(_tok(pem, SUPA))) is not None
