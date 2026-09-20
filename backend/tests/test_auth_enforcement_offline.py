"""End-to-end auth enforcement over ASGI (real middleware, real routers, real
JWT + service-credential verification; only the database is a fake).

Proves: 401/403 semantics, scope gating of previously-open mutating routes,
deactivated accounts fail closed, workers are services not users, header/body
identity cannot escalate, legacy admin key behaviour, one identity resolution
per request, and that MCP shares the same verification + scope vocabulary.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import admin, agent_store, approval, decompose, goals, ingest, problems, runs
from app.api import deps
from app.api.deps import get_auth_context, get_scope, require_scopes
from app.services import auth_context as ac
from app.services.access import visibility_predicate
from app.services.authn import (
    OidcConfig, StaticJwks, jwk_from_public_numbers, make_actor_middleware,
)
from app.services.service_identity import (
    ServiceRecord, ServiceTokenConfig, StaticServiceRegistry, mint_service_token, verify_service_token,
)

ISS, AUD, KID = "https://p.supabase.co/auth/v1", "authenticated", "kid-1"
SVC_CFG = ServiceTokenConfig("stealth-svc", "stealth-api", {"k1": "s" * 48}, "test")
TA = "aaaaaaaa-0000-0000-0000-00000000000a"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pem(rsa_key):
    return rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


@pytest.fixture(scope="module")
def jwks(rsa_key):
    n = rsa_key.public_key().public_numbers()
    return StaticJwks({KID: jwk_from_public_numbers(
        n.n.to_bytes((n.n.bit_length() + 7) // 8, "big"), n.e.to_bytes((n.e.bit_length() + 7) // 8, "big"), KID)})


def jwt_for(pem, sub, *, iss=ISS, aud=AUD, exp=600, extra=None):
    now = int(time.time())
    return pyjwt.encode({"iss": iss, "aud": aud, "sub": sub, "iat": now, "exp": now + exp, **(extra or {})},
                        pem, algorithm="RS256", headers={"kid": KID})


class FakePool:
    """users / memberships / platform roles / audit rows, in memory."""

    def __init__(self):
        self.users = {}                    # subject -> dict(id,is_active,t_expired)
        self.orgs = {}                     # user id -> [(org, role)]
        self.platform = {}                 # user id -> [role]
        self.audit = []
        self.resolutions = 0

    def add_user(self, subject, *, active=True, orgs=(), platform=()):
        uid = f"uid-{subject}"
        self.users[subject] = {"id": uid, "is_active": active, "t_expired": None}
        self.orgs[uid] = list(orgs)
        self.platform[uid] = list(platform)
        return uid

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if q.startswith("SELECT id, is_active"):
            self.resolutions += 1
            return self.users.get(a[1])
        if q.startswith("INSERT INTO users"):
            uid = self.add_user(a[1])
            return {"id": uid}
        if q.startswith("INSERT INTO audit_events"):
            self.audit.append({"actor_subject": a[0], "action": a[2], "object_id": a[4], "details": a[6]})
            return {"id": len(self.audit)}
        return None

    async def fetch(self, sql, *a):
        if "platform_role_grants" in sql:
            return [{"role": r} for r in self.platform.get(a[0], [])]
        if "FROM org_memberships" in sql:
            return [{"organization_id": o, "organization_name": o, "organization_slug": o, "role_name": r}
                    for o, r in self.orgs.get(a[0], [])]
        return []


REGISTRY = StaticServiceRegistry([
    ServiceRecord("ingest-svc", frozenset({ac.INGESTION_PROCESS, ac.PROJECTION_WRITE}), frozenset({"ingestion_worker"}), "test"),
    ServiceRecord("submitter-svc", frozenset({ac.INGESTION_SUBMIT}), frozenset(), "test"),
    ServiceRecord("maint-svc", frozenset({ac.MAINTENANCE_RUN}), frozenset({"maintenance_worker"}), "test"),
])


def svc_token(service_id, scopes):
    return mint_service_token(SVC_CFG, service_id=service_id, scopes=scopes)[0]


@pytest.fixture()
def env(monkeypatch, jwks):
    """(client, pool). OIDC + service tokens configured => auth is enforced."""
    for k, v in {"supabase_project_url": "https://p.supabase.co", "supabase_jwt_audience": AUD,
                 "oidc_issuer": None, "oidc_audience": None, "admin_api_key": None,
                 "admin_api_key_legacy_enabled": True, "service_token_keys": "k1:" + "s" * 48}.items():
        monkeypatch.setattr(deps.settings, k, v, raising=False)
    pool = FakePool()
    app = FastAPI()

    async def verify_svc(token):
        return await verify_service_token(token, config=SVC_CFG, registry=REGISTRY)

    app.add_middleware(make_actor_middleware(
        OidcConfig.from_settings(deps.settings), jwks, private_visibility_enabled=False, service_verifier=verify_svc))
    for r in (agent_store.router, approval.router, decompose.router, ingest.router, problems.router, runs.router,
              admin.router, goals.router):
        app.include_router(r)
    app.state.pool = pool

    @app.get("/probe/scope")
    async def probe_scope(scope=Depends(get_scope)):
        sql, params = visibility_predicate(scope, param_index=1)
        return {"viewer": scope.viewer_id, "orgs": list(scope.org_ids), "sql": sql, "params": params}

    @app.get("/probe/ctx")
    async def probe_ctx(ctx=Depends(get_auth_context), _=Depends(require_scopes(ac.KNOWLEDGE_READ))):
        return {"kind": ctx.kind, "id": ctx.actor_id, "scopes": sorted(ctx.scopes),
                "tenant": getattr(ctx, "tenant_id", None), "resolutions": pool.resolutions}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app, raise_server_exceptions=False), pool


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def svc_hdr(t):
    return {"X-Stealth-Service-Token": t}


GATED = [
    ("post", "/v1/agent-store/submit"), ("post", "/v1/agent-store/promote"), ("post", "/v1/agent-store/00000000-0000-0000-0000-000000000001/decide"),
    ("get", "/v1/agent-store/pending"), ("post", "/v1/approvals/00000000-0000-0000-0000-000000000001"),
    ("get", "/v1/approvals/pending"), ("post", "/v1/decompose/00000000-0000-0000-0000-000000000001/decide"),
    ("get", "/v1/decompose/pending"), ("post", "/v1/traces"), ("post", "/v1/problems"), ("post", "/v1/benchmarks"),
    ("post", "/v1/evaluations"), ("post", "/v1/runs/r1/resume"), ("post", "/v1/runs/r1/nodes/1/retry"),
    ("post", "/v1/goals"), ("get", "/v1/admin/index-lag"), ("post", "/v1/admin/extractors"),
]


# ------------------------------------------------------------- authentication

@pytest.mark.parametrize("method,path", GATED)
def test_anonymous_is_401_on_every_previously_open_mutating_route(env, method, path):
    client, _ = env
    r = client.request(method.upper(), path, json={} if method == "post" else None)
    assert r.status_code == 401, (path, r.status_code, r.text[:120])


def test_public_paths_stay_public(env):
    client, _ = env
    assert client.get("/health").status_code == 200
    r = client.get("/probe/scope")
    assert r.status_code == 200 and r.json()["viewer"] is None and "'public'" in r.json()["sql"]
    assert client.get("/probe/ctx").status_code == 401         # authenticated-by-default when scope-gated


def test_bad_tokens_are_401_never_anonymous(env, pem):
    client, pool = env
    pool.add_user("alice")
    now = int(time.time())
    bad = {
        "expired": jwt_for(pem, "alice", exp=-3600),
        "wrong_issuer": jwt_for(pem, "alice", iss="https://evil.supabase.co/auth/v1"),
        "wrong_audience": jwt_for(pem, "alice", aud="anon"),
        "garbage": "not.a.jwt",
        "hs256_confusion": pyjwt.encode({"iss": ISS, "aud": AUD, "sub": "alice", "exp": now + 60}, "k" * 40, algorithm="HS256",
                                        headers={"kid": KID}),
        "no_subject": pyjwt.encode({"iss": ISS, "aud": AUD, "exp": now + 60}, pem, algorithm="RS256", headers={"kid": KID}),
        "unknown_kid": pyjwt.encode({"iss": ISS, "aud": AUD, "sub": "alice", "exp": now + 60}, pem, algorithm="RS256", headers={"kid": "nope"}),
    }
    for name, tok in bad.items():
        r = client.get("/probe/scope", headers=bearer(tok))
        assert r.status_code == 401, (name, r.status_code)
        assert tok not in r.text                                 # never echo the credential


def test_valid_supabase_jwt_resolves_one_canonical_context(env, pem):
    client, pool = env
    pool.add_user("alice", orgs=[(TA, "member")])
    r = client.get("/probe/ctx", headers=bearer(jwt_for(pem, "alice")))
    j = r.json()
    assert r.status_code == 200 and j["kind"] == "user" and j["id"] == "alice" and j["tenant"] == TA
    assert ac.KNOWLEDGE_PUBLISH not in j["scopes"] and ac.KNOWLEDGE_READ in j["scopes"]
    assert j["resolutions"] == 1, "identity must be resolved once per request, not per dependency"


# ------------------------------------------------------------- authorization

def test_plain_user_is_403_on_publish_gated_routes_and_denial_is_audited(env, pem):
    client, pool = env
    pool.add_user("alice")
    h = bearer(jwt_for(pem, "alice"))
    assert client.post("/v1/agent-store/promote", json={}, headers=h).status_code == 403
    assert client.post("/v1/approvals/00000000-0000-0000-0000-000000000001", json={}, headers=h).status_code == 403
    assert client.get("/v1/admin/index-lag", headers=h).status_code == 403
    ev = [e for e in pool.audit if e["action"] == "access.denied"]
    assert ev and ev[0]["actor_subject"] == "alice" and "knowledge:publish" in ev[0]["details"]
    assert all("eyJ" not in str(e) for e in pool.audit), "no raw JWTs in audit rows"


def test_reviewer_platform_role_grants_publish_scope_but_not_admin(env, pem):
    client, pool = env
    pool.add_user("rev", platform=["reviewer"])
    h = bearer(jwt_for(pem, "rev"))
    assert client.post("/v1/agent-store/promote", json={}, headers=h).status_code not in (401, 403)
    assert client.get("/v1/admin/index-lag", headers=h).status_code == 403


def test_platform_admin_reaches_admin_routes(env, pem):
    client, pool = env
    pool.add_user("root", platform=["platform_admin"])
    assert client.get("/v1/admin/index-lag", headers=bearer(jwt_for(pem, "root"))).status_code not in (401, 403)


def test_org_admin_is_not_a_platform_admin(env, pem):
    client, pool = env
    pool.add_user("oa", orgs=[(TA, "owner")])
    assert client.get("/v1/admin/index-lag", headers=bearer(jwt_for(pem, "oa"))).status_code == 403


def test_deactivated_account_is_403_on_read_and_write_paths(env, pem):
    """Regression: get_scope used to swallow IdentityInactive and serve the user."""
    client, pool = env
    pool.add_user("gone", active=False)
    h = bearer(jwt_for(pem, "gone"))
    for path in ("/probe/scope", "/probe/ctx"):
        assert client.get(path, headers=h).status_code == 403, path
    assert client.post("/v1/goals", json={"name": "x"}, headers=h).status_code == 403


def test_spoofed_viewer_header_never_overrides_the_verified_subject(env, pem):
    client, pool = env
    pool.add_user("bob", orgs=[("bbbbbbbb-0000-0000-0000-00000000000b", "member")])
    r = client.get("/probe/scope", headers={**bearer(jwt_for(pem, "bob")), "X-Viewer-Id": "alice"})
    assert r.json()["viewer"] == "bob" and "alice" not in r.json()["params"]
    assert client.get("/probe/scope", headers={"X-Viewer-Id": "alice"}).json()["viewer"] is None


def test_membership_removal_takes_effect_on_the_next_request(env, pem):
    client, pool = env
    uid = pool.add_user("dave", orgs=[(TA, "member")])
    h = bearer(jwt_for(pem, "dave"))
    assert client.get("/probe/scope", headers=h).json()["orgs"] == [TA]
    pool.orgs[uid] = []
    assert client.get("/probe/scope", headers=h).json()["orgs"] == []
    pool.platform[uid] = ["platform_admin"]                       # role grant is likewise live
    assert client.get("/v1/admin/index-lag", headers=h).status_code not in (401, 403)
    pool.platform[uid] = []
    assert client.get("/v1/admin/index-lag", headers=h).status_code == 403


# ------------------------------------------------------------- service identity

def test_service_credential_authenticates_as_a_service_not_a_user(env):
    client, pool = env
    r = client.get("/probe/scope", headers=svc_hdr(svc_token("ingest-svc", [ac.INGESTION_PROCESS])))
    j = r.json()
    assert r.status_code == 200 and j["viewer"] is None and "'public'" in j["sql"] and j["orgs"] == []
    assert pool.resolutions == 0, "a service never provisions/looks up a users row"


def test_worker_scopes_are_least_privilege(env):
    client, _ = env
    ingest_only = svc_hdr(svc_token("ingest-svc", [ac.INGESTION_PROCESS]))
    assert client.post("/v1/traces", json={}, headers=ingest_only).status_code == 403          # needs ingestion:submit
    assert client.get("/v1/admin/index-lag", headers=ingest_only).status_code == 403           # admin op
    assert client.post("/v1/agent-store/promote", json={}, headers=ingest_only).status_code == 403
    assert client.post("/v1/goals", json={"name": "x"}, headers=ingest_only).status_code == 403  # user-only route
    assert client.post("/v1/traces", json={}, headers=svc_hdr(svc_token("submitter-svc", [ac.INGESTION_SUBMIT]))).status_code not in (401, 403)
    assert client.get("/v1/admin/index-lag", headers=svc_hdr(svc_token("maint-svc", [ac.MAINTENANCE_RUN]))).status_code not in (401, 403)


def test_invalid_expired_revoked_and_escalated_service_credentials_are_401(env):
    client, _ = env
    expired = mint_service_token(SVC_CFG, service_id="ingest-svc", scopes=[ac.INGESTION_PROCESS], now=time.time() - 7200, ttl_seconds=60)[0]
    tok, jti = mint_service_token(SVC_CFG, service_id="ingest-svc", scopes=[ac.INGESTION_PROCESS])
    escalated = svc_token("ingest-svc", [ac.INGESTION_PROCESS, ac.ADMIN_OPS])
    unknown = svc_token("nobody", [ac.INGESTION_PROCESS])
    for name, t in (("expired", expired), ("escalated", escalated), ("unknown", unknown), ("garbage", "xxx")):
        assert client.get("/probe/scope", headers=svc_hdr(t)).status_code == 401, name
    assert client.get("/probe/scope", headers=svc_hdr(tok)).status_code == 200
    REGISTRY.revoke(jti)
    try:
        assert client.get("/probe/scope", headers=svc_hdr(tok)).status_code == 401
    finally:
        REGISTRY.revoked.discard(jti)


def test_user_jwt_in_the_service_header_and_service_token_as_bearer_are_rejected(env, pem):
    client, pool = env
    pool.add_user("alice")
    assert client.get("/probe/scope", headers=svc_hdr(jwt_for(pem, "alice"))).status_code == 401
    assert client.get("/probe/scope", headers=bearer(svc_token("ingest-svc", [ac.INGESTION_PROCESS]))).status_code == 401


def test_presenting_both_credentials_is_refused(env, pem):
    client, pool = env
    pool.add_user("alice")
    r = client.get("/probe/scope", headers={**bearer(jwt_for(pem, "alice")), **svc_hdr(svc_token("ingest-svc", [ac.INGESTION_PROCESS]))})
    assert r.status_code == 400


def test_service_token_when_unconfigured_is_401_not_ignored(jwks):
    app = FastAPI()
    app.add_middleware(make_actor_middleware(None, None, private_visibility_enabled=False, service_verifier=None))

    @app.get("/x")
    async def x():
        return {}

    assert TestClient(app).get("/x", headers=svc_hdr("anything")).status_code == 401


# ------------------------------------------------------------- legacy admin key

def test_legacy_admin_key_still_works_is_audited_and_can_be_retired(env, monkeypatch):
    client, pool = env
    key = "k" * 40
    monkeypatch.setattr(deps.settings, "admin_api_key", key, raising=False)
    assert client.get("/v1/admin/index-lag", headers={"X-Admin-Api-Key": "wrong"}).status_code == 401
    assert client.get("/v1/admin/index-lag", headers={"X-Admin-Api-Key": key}).status_code not in (401, 403)
    assert any(e["action"] == "admin.legacy_key_used" for e in pool.audit)
    assert key not in str(pool.audit)
    monkeypatch.setattr(deps.settings, "admin_api_key_legacy_enabled", False, raising=False)
    assert client.get("/v1/admin/index-lag", headers={"X-Admin-Api-Key": key}).status_code == 401


def test_user_with_admin_header_but_no_scope_cannot_bypass_via_wrong_key(env, pem, monkeypatch):
    client, pool = env
    pool.add_user("alice")
    monkeypatch.setattr(deps.settings, "admin_api_key", "k" * 40, raising=False)
    r = client.get("/v1/admin/index-lag", headers={**bearer(jwt_for(pem, "alice")), "X-Admin-Api-Key": "wrong"})
    assert r.status_code == 401


# ------------------------------------------------------------- unenforced posture is TEST-only

def test_unenforced_posture_exists_only_in_test_without_identity(monkeypatch):
    for k in ("supabase_project_url", "supabase_jwt_audience", "oidc_issuer", "oidc_audience", "service_token_keys"):
        monkeypatch.setattr(deps.settings, k, None, raising=False)
    assert deps.auth_enforced() is False                       # pytest => TEST + nothing configured
    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    assert deps.auth_enforced() is True
    monkeypatch.setenv("STEALTHLAB_ENV", "STAGING")
    assert deps.auth_enforced() is True
    monkeypatch.setenv("STEALTHLAB_ENV", "bogus")              # unknown env resolves to PRODUCTION
    assert deps.auth_enforced() is True


# ------------------------------------------------------------- MCP shares the same verification

@pytest.fixture(scope="module")
def mcp():
    import os

    from pathlib import Path

    from dotenv import dotenv_values

    before = dict(os.environ)
    declared = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("STEALTHLAB_MCP_TOKEN")
    os.environ.setdefault("STEALTHLAB_MCP_TOKEN", declared or "t" * 40)   # server.py refuses a mismatch with .env
    import app.mcp_server.server as srv

    for k in set(os.environ) - set(before):
        del os.environ[k]
    return srv


def _verifier(mcp, jwks, *, allow_shared=True, pool=None):
    mcp._LIFESPAN_STATE["pool"] = pool
    reg = StaticServiceRegistry(list(REGISTRY.services.values()))
    return mcp.OidcAwareTokenVerifier(
        "shared-secret-value", OidcConfig(ISS, AUD, "https://x/jwks", ("RS256",)), jwks,
        service_config=SVC_CFG, service_registry_factory=lambda: reg, allow_shared_token=allow_shared)


def test_mcp_human_token_carries_the_same_scopes_and_orgs_as_rest(mcp, jwks, pem):
    pool = FakePool()
    pool.add_user("alice", orgs=[(TA, "member")], platform=["reviewer"])
    try:
        at = asyncio.run(_verifier(mcp, jwks, pool=pool).verify_token(jwt_for(pem, "alice")))
        assert at.subject == "alice" and f"org:{TA}" in at.scopes and ac.KNOWLEDGE_PUBLISH in at.scopes
        scope = mcp._scope_from_token(at)
        assert scope.viewer_id == "alice" and scope.org_ids == (TA,)      # identical to REST get_scope
    finally:
        mcp._LIFESPAN_STATE.pop("pool", None)


def test_mcp_rejects_deactivated_users_and_bad_tokens(mcp, jwks, pem):
    pool = FakePool()
    pool.add_user("gone", active=False)
    try:
        v = _verifier(mcp, jwks, pool=pool)
        assert asyncio.run(v.verify_token(jwt_for(pem, "gone"))) is None
        assert asyncio.run(v.verify_token(jwt_for(pem, "gone", iss="https://evil/auth/v1"))) is None
        assert asyncio.run(v.verify_token("garbage")) is None
    finally:
        mcp._LIFESPAN_STATE.pop("pool", None)


def test_mcp_service_token_has_no_subject_and_only_its_scopes(mcp, jwks):
    at = asyncio.run(_verifier(mcp, jwks).verify_token(svc_token("ingest-svc", [ac.INGESTION_PROCESS])))
    assert at.subject is None and at.client_id == "service:ingest-svc"
    assert ac.INGESTION_PROCESS in at.scopes and ac.RETRIEVAL_READ not in at.scopes and ac.KNOWLEDGE_PUBLISH not in at.scopes


def test_mcp_shared_secret_disabled_in_shared_mode(mcp, jwks):
    assert asyncio.run(_verifier(mcp, jwks, allow_shared=False).verify_token("shared-secret-value")) is None
    at = asyncio.run(_verifier(mcp, jwks, allow_shared=True).verify_token("shared-secret-value"))
    assert at.client_id == "stealthlab-local" and "local:operator" in at.scopes


def test_mcp_tool_scope_enforcement(mcp, monkeypatch):
    def as_token(scopes):
        monkeypatch.setattr(mcp, "get_access_token", lambda: mcp.AccessToken(token="t", client_id="c", scopes=scopes))

    as_token(["stealthlab:tools", ac.RETRIEVAL_READ])
    mcp._enforce_tool_scope("search_procedures")                               # read ok
    for tool in ("submit_procedure", "find_best_way", "decide_procedure", "ingest_trajectory", "brand_new_tool"):
        with pytest.raises(PermissionError):
            mcp._enforce_tool_scope(tool)
    as_token(["stealthlab:tools", *ac.USER_BASELINE_SCOPES])
    mcp._enforce_tool_scope("submit_procedure"); mcp._enforce_tool_scope("find_best_way")
    with pytest.raises(PermissionError):
        mcp._enforce_tool_scope("decide_procedure")                            # publish is not a baseline scope
    as_token(["stealthlab:tools", ac.KNOWLEDGE_PUBLISH])
    mcp._enforce_tool_scope("decide_procedure")
    as_token(["stealthlab:tools", "svc:ingest-svc", ac.INGESTION_PROCESS])     # worker over MCP
    for tool in ("search_procedures", "submit_procedure", "find_best_way"):
        with pytest.raises(PermissionError):
            mcp._enforce_tool_scope(tool)


def test_every_registered_mcp_tool_is_classified(mcp):
    import re

    src = open(mcp.__file__, encoding="utf-8").read()
    names = set(re.findall(r"@server\.tool\([^)]*\)\s*\nasync def (\w+)\(", src))
    assert len(names) > 40
    unclassified = sorted(n for n in names if n not in mcp._TOOL_SCOPES)
    assert not unclassified, f"classify these MCP tools in _TOOL_SCOPES (default is deny-to-readers): {unclassified}"


def test_mcp_data_routes_never_use_unrestricted_scope(mcp):
    src = open(mcp.__file__, encoding="utf-8").read()
    assert "scope=AccessScope.unrestricted()" not in src


def test_mcp_route_gate_local_vs_remote(mcp, monkeypatch):
    def req(host, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})

    monkeypatch.setattr(mcp.settings, "deployment_mode", "single_user", raising=False)
    assert mcp._is_local_request(req("127.0.0.1"))
    assert not mcp._is_local_request(req("10.0.0.5"))
    assert not mcp._is_local_request(req("127.0.0.1", {"x-forwarded-for": "8.8.8.8"}))   # behind a proxy
    monkeypatch.setattr(mcp.settings, "deployment_mode", "shared", raising=False)
    assert not mcp._is_local_request(req("127.0.0.1"))
