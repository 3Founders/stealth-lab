"""Who may call the Delegator's website routes: a Supabase WEBSITE-SESSION token, not an MCP OAuth token, not nothing."""
import asyncio
from types import SimpleNamespace

import pytest

from app.mcp_server import delegate_routes as dr
from app.services import authn
from app.services import delegate as dg

CFG = authn.OidcConfig(issuer="https://p.supabase.co/auth/v1", audience="authenticated",
                       jwks_url="https://p.supabase.co/auth/v1/.well-known/jwks.json", allowed_algs=("ES256",))


def _req(token=None, host="203.0.113.5"):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def _gate(cfg=CFG, local=False):
    return dr.Gate(SimpleNamespace(_oidc_config=cfg, _jwks_provider=object()), lambda: None, lambda r: local)


def _stub(monkeypatch, claims=None, reject=False):
    async def fake(token, config, jwks_provider):
        if reject:
            raise authn.TokenRejected("bad")
        return authn.Actor(subject="user-1", issuer=CFG.issuer, claims=claims or {})
    monkeypatch.setattr(authn, "validate_token_async", fake)


def run(coro):
    return asyncio.run(coro)


def test_a_website_session_token_is_accepted_and_scopes_to_its_subject(monkeypatch):
    _stub(monkeypatch)
    assert run(_gate().subject(_req("tok"))) == ("user-1", None)


def test_an_mcp_oauth_token_is_refused_on_the_website_routes(monkeypatch):
    _stub(monkeypatch, claims={"client_id": "some-mcp-app"})
    sub, why = run(_gate().subject(_req("tok")))
    assert sub is None and why[1] == 401 and "MCP" in why[0]


def test_no_token_and_an_invalid_token_are_401(monkeypatch):
    assert run(_gate().subject(_req()))[1][1] == 401
    _stub(monkeypatch, reject=True)
    assert run(_gate().subject(_req("tok")))[1][1] == 401


def test_with_no_identity_provider_only_loopback_is_the_local_user():
    assert run(_gate(cfg=None, local=True).subject(_req())) == ("local-user", None)
    assert run(_gate(cfg=None, local=False).subject(_req()))[1][1] == 503


def test_cors_allows_only_listed_origins_and_never_credentials(monkeypatch):
    monkeypatch.delenv("DELEGATE_ALLOWED_ORIGINS", raising=False)
    ok = dr.cors_headers(SimpleNamespace(headers={"origin": "https://www.kell.org.in"}))
    assert ok["Access-Control-Allow-Origin"] == "https://www.kell.org.in"
    assert "Access-Control-Allow-Credentials" not in ok
    assert "Access-Control-Allow-Origin" not in dr.cors_headers(SimpleNamespace(headers={"origin": "https://evil.example"}))
    monkeypatch.setenv("DELEGATE_ALLOWED_ORIGINS", "https://app.example")
    assert "Access-Control-Allow-Origin" not in dr.cors_headers(SimpleNamespace(headers={"origin": "https://www.kell.org.in"}))


@pytest.mark.parametrize("text,names", [
    ("@alice/web-chat do this", ["web-chat"]),
    ("@web-chat and @opencode, then @web-chat again", ["web-chat", "opencode"]),
    ("email me at a@b.com about it", []),
    ("path/@scope/pkg is not a mention", []),
])
def test_mentions(text, names):
    assert dg.mentions(text) == names
