"""The website's OpenAI-compatible chat on the hosted MCP service: who pays, who may call, what the browser may send."""
import asyncio
from types import SimpleNamespace

from app.mcp_server import chat_routes as cr

PILOT = "3b34acf9-4b19-4678-a09a-43d487707eaf"
OWN = "aaaaaaaa-0000-0000-0000-000000000001"


def test_an_unnamed_org_is_the_pilot_org_when_the_person_is_in_it():
    assert cr.paying_org(None, (OWN, PILOT), PILOT) == (PILOT, None)
    assert cr.paying_org(None, (OWN,), PILOT) == (None, None)          # not enrolled: the governed path decides
    assert cr.paying_org(None, (OWN, PILOT), None) == (None, None)     # no pilot configured: nothing guessed


def test_a_named_org_must_be_one_the_person_belongs_to():
    assert cr.paying_org(OWN.upper(), (OWN, PILOT), PILOT) == (OWN, None)
    org, why = cr.paying_org("bbbbbbbb-0000-0000-0000-000000000002", (OWN,), PILOT)
    assert org is None and "not a member" in why


def test_cors_names_the_headers_the_site_sends_and_never_a_wildcard(monkeypatch):
    monkeypatch.delenv("DELEGATE_ALLOWED_ORIGINS", raising=False)
    ok = cr.cors_headers(SimpleNamespace(headers={"origin": "https://www.kell.org.in"}))
    assert ok["Access-Control-Allow-Origin"] == "https://www.kell.org.in"
    for h in ("authorization", "content-type", "x-stealthlab-data-class", "x-stealthlab-org"):
        assert h in ok["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Credentials" not in ok and "*" not in ok["Access-Control-Allow-Origin"]
    assert "Access-Control-Allow-Origin" not in cr.cors_headers(SimpleNamespace(headers={"origin": "https://evil.example"}))


def test_no_token_is_401_and_an_unconfigured_remote_is_503():
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.5"))
    verifier = SimpleNamespace(_oidc_config=object(), _jwks_provider=object())
    _, why = asyncio.run(cr.resolve_identity(verifier, object(), req, lambda r: False))
    assert why[1] == 401
    _, why = asyncio.run(cr.resolve_identity(SimpleNamespace(_oidc_config=None), None, req, lambda r: False))
    assert why[1] == 503
    who, _ = asyncio.run(cr.resolve_identity(SimpleNamespace(_oidc_config=None), None, req, lambda r: True))
    assert who.actor_id == "local-user"


def test_errors_use_the_openai_envelope_and_a_policy_refusal_is_403():
    from app.providers.types import ProviderCallDenied
    req = SimpleNamespace(headers={})
    r = cr._failure(req, ProviderCallDenied("per-user daily budget exceeded"))
    assert r.status_code == 403
    assert b'"policy_denied"' in r.body and b'"error"' in r.body
    assert cr._failure(req, ProviderCallDenied("no connection available to you offers 'x'")).status_code == 404
