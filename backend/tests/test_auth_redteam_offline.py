"""Red-team + closure tests for the remaining auth items: job authority on every
enqueue, rate-limit classes, legacy admin key default, break-glass, audited
operator actions, authorized blob hydration, injection/IDOR probes. Offline."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import auth_context as ac
from app.services.access import AccessScope, visibility_predicate
from app.services.auth_context import AuthContext, JobAuthority, ServiceAuthContext, resolve_auth_context
from app.services.authn import Actor
from app.services.authorization import Action, AuthorizationDenied, ObjectRef, authorize, can_read


def run(c):
    return asyncio.run(c)


# ------------------------------------------------------------ job authority everywhere

class _CapPool:
    def __init__(self):
        self.args = None

    async def fetchrow(self, sql, *a):
        self.args = a
        return {"id": 1}


def test_every_enqueue_carries_authority_even_without_an_explicit_one():
    from app.ingestion import queue as q

    p = _CapPool()
    run(q.enqueue(p, "skill_package", {}, idempotency_key="k", scope_type="user", owner_id="alice", visibility="private", offload=False))
    a = p.args
    assert a[10] == "alice" and a[13] == "user_private" and a[14] == "private" and a[-1] is False
    run(q.enqueue(p, "skill_package", {}, idempotency_key="k2", scope_type="global", visibility="public", offload=False))
    assert p.args[10] is None and p.args[13] == "global_public" and p.args[-1] is False


def test_skill_package_enqueue_stamps_global_authority_and_submitter():
    from app.ingestion import enqueue as e

    p = _CapPool()
    run(e.enqueue_skill_packages(p, [{"source_id": "s", "repo": "r", "commit": "c", "path": "p"}], submitted_by="github-actions-ingestion"))
    assert p.args[11] == "github-actions-ingestion" and p.args[13] == "global_public" and p.args[-1] is False


# ------------------------------------------------------------ rate limit classes

def test_rate_limits_scale_by_verified_caller_class():
    from app.services.governance import RateLimit, scale_limit_for_class

    base = RateLimit(max_requests=100, window=timedelta(hours=1))
    assert scale_limit_for_class(base, "ip:1.2.3.4").max_requests == 50          # anonymous: stricter
    assert scale_limit_for_class(base, "viewer:alice").max_requests == 100
    assert scale_limit_for_class(base, "service:cloud-run-ingestion").max_requests == 500
    assert scale_limit_for_class(RateLimit(1, timedelta(hours=1)), "ip:x").max_requests == 1


def test_scope_key_uses_verified_service_id_not_ip(monkeypatch):
    from app.api import deps
    from app.services import authn

    svc = ServiceAuthContext("ingest-svc", scopes=frozenset())
    tok = authn.set_current_service(svc)
    try:
        req = SimpleNamespace(client=SimpleNamespace(host="9.9.9.9"))
        assert deps.scope_key_for(AccessScope.anonymous(), req) == "service:ingest-svc"
    finally:
        authn.reset_current_service(tok)


# ------------------------------------------------------------ legacy admin key default

def test_legacy_admin_key_is_opt_in_in_production(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    assert Settings().legacy_admin_key_enabled is False
    assert Settings(admin_api_key_legacy_enabled=True).legacy_admin_key_enabled is True
    monkeypatch.setenv("STEALTHLAB_ENV", "TEST")
    assert Settings().legacy_admin_key_enabled is True
    assert Settings(admin_api_key_legacy_enabled=False).legacy_admin_key_enabled is False


# ------------------------------------------------------------ break-glass

class _BGPool:
    def __init__(self, active=False):
        self.active, self.audit, self.grants = active, [], []

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if q.startswith("SELECT id, is_active"):
            return {"id": "uid-x", "is_active": True, "t_expired": None}
        if "FROM break_glass_grants" in q:
            return {"?": 1} if self.active else None
        if q.startswith("INSERT INTO audit_events"):
            self.audit.append((a[0], a[2], a[4], a[6]))
            return {"id": len(self.audit)}
        if q.startswith("INSERT INTO break_glass_grants"):
            self.grants.append(a)
            return {"id": 7}

    async def fetch(self, sql, *a):
        return []

    async def execute(self, *a):
        return "OK"


ADMIN = AuthContext("uid-admin", "admin", scopes=ac.scopes_for_roles({"user", "platform_admin"}))
PLAIN = AuthContext("uid-plain", "plain")


def test_break_glass_requires_admin_reason_ttl_and_two_people():
    from app.services.service_identity import BreakGlassDenied, grant_break_glass

    p = _BGPool()
    good = "investigating tenant A support ticket 4711"
    with pytest.raises(BreakGlassDenied):
        run(grant_break_glass(p, grantor=PLAIN, grantee_user_id="u2", reason=good))              # not auth:admin
    with pytest.raises(BreakGlassDenied):
        run(grant_break_glass(p, grantor=ADMIN, grantee_user_id="uid-admin", reason=good))       # self-grant
    with pytest.raises(BreakGlassDenied):
        run(grant_break_glass(p, grantor=ADMIN, grantee_user_id="u2", reason="because"))         # no real reason
    for ttl in (0, 3601):
        with pytest.raises(BreakGlassDenied):
            run(grant_break_glass(p, grantor=ADMIN, grantee_user_id="u2", reason=good, ttl_seconds=ttl))
    assert not p.grants
    assert run(grant_break_glass(p, grantor=ADMIN, grantee_user_id="u2", reason=good, ttl_seconds=600)) == "7"
    assert p.audit and p.audit[0][1] == "break_glass.granted" and good in p.audit[0][3]


def test_break_glass_adds_cross_tenant_only_while_active_and_never_by_role():
    actor = Actor(subject="dave", issuer="https://p.supabase.co/auth/v1")
    off = run(resolve_auth_context(_BGPool(active=False), actor))
    on = run(resolve_auth_context(_BGPool(active=True), actor))
    assert ac.TENANCY_CROSS not in off.scopes and not off.break_glass
    assert ac.TENANCY_CROSS in on.scopes and on.break_glass
    other = ObjectRef("org", owner_id="x", tenant_id="tttt")
    assert not can_read(off, other) and can_read(on, other)
    assert not can_read(on, ObjectRef("system")), "break-glass never opens SYSTEM_INTERNAL"


# ------------------------------------------------------------ audited operator actions

def test_service_registration_issue_revoke_disable_are_audited_without_secrets():
    from app.services import service_identity as si

    p = _BGPool()
    cfg = si.ServiceTokenConfig("i", "a", {"k1": "s" * 48}, "production")
    run(si.register_service(p, service_id="ingest", scopes=[ac.INGESTION_PROCESS], environment="production", created_by="op"))
    token = run(si.issue_credential(p, cfg, service_id="ingest", scopes=[ac.INGESTION_PROCESS], ttl_seconds=600, created_by="op"))
    run(si.revoke_credential(p, credential_id="jti1", reason="leaked", actor="op"))
    run(si.disable_service(p, service_id="ingest", actor="op"))
    actions = [a[1] for a in p.audit]
    assert actions == ["service.registered", "service.credential_issued", "service.credential_revoked", "service.disabled"]
    assert token not in str(p.audit) and "s" * 48 not in str(p.audit)


# ------------------------------------------------------------ authorized hydration

def test_blob_hydration_requires_read_authority_on_the_owning_object():
    from app.services.object_storage import BLOB_KEY, MemoryStore, authorized_hydrate, offload_payload  # noqa: F401

    store = MemoryStore()
    payload = {"text": "secret", "n": 1}
    svc = ServiceAuthContext("w", scopes=frozenset({ac.INGESTION_PROCESS}))
    alice_job = svc.bind_job(JobAuthority(submitted_by_user_id="alice", scope="user_private"))
    alices = ObjectRef("private", owner_id="alice")
    bobs = ObjectRef("private", owner_id="bob")
    assert run(authorized_hydrate(payload, alice_job, alices, store=store)) == payload
    with pytest.raises(AuthorizationDenied):
        run(authorized_hydrate(payload, alice_job, bobs, store=store))
    with pytest.raises(AuthorizationDenied):
        run(authorized_hydrate(payload, svc, alices, store=store))          # unbound service


# ------------------------------------------------------------ injection / IDOR probes

HOSTILE = ["alice' OR '1'='1", "x'; DROP TABLE procedures;--", "\" OR 1=1 --", "a\x00b", "1) OR (1=1"]


@pytest.mark.parametrize("v", HOSTILE)
def test_hostile_identity_strings_are_bound_parameters_never_sql(v):
    scope = AccessScope.for_org_member(v, [v, "00000000-0000-0000-0000-000000000001"])
    sql, params = visibility_predicate(scope, alias="p", param_index=3)
    assert v not in sql and "DROP" not in sql and "OR 1=1" not in sql.replace("visibility", "")
    assert params[0] == v and v in params
    import re
    assert set(re.findall(r"\$(\d+)", sql)) == {str(i) for i in range(3, 3 + len(params))}


def test_idor_probe_private_object_is_indistinguishable_from_missing():
    mine = AuthContext("u1", "alice")
    theirs, missing = ObjectRef("private", owner_id="bob"), ObjectRef("private", owner_id="nobody")
    msgs = []
    for obj in (theirs, missing):
        with pytest.raises(AuthorizationDenied) as e:
            authorize(mine, Action.READ, obj, hide_existence=True)
        msgs.append((e.value.status, str(e.value)))
    assert msgs[0] == msgs[1] == (404, "not found")


def test_predicate_for_service_and_anonymous_is_public_only_regardless_of_input():
    for scope in (ServiceAuthContext("s", scopes=frozenset({ac.ADMIN_OPS})).access_scope(), AccessScope.anonymous()):
        sql, params = visibility_predicate(scope)
        assert sql == "visibility = 'public'" and params == []


def test_replayed_credential_from_another_service_is_rejected():
    """Token replay/reuse across identities: jti is bound to its service_id in the registry."""
    import time

    from app.services.service_identity import (ServiceRecord, ServiceTokenConfig, ServiceTokenRejected, StaticServiceRegistry,
                                               mint_service_token, verify_service_token)

    cfg = ServiceTokenConfig("i", "a", {"k1": "s" * 48}, "production")
    rec = lambda n: ServiceRecord(n, frozenset({ac.INGESTION_PROCESS}), frozenset(), "production")   # noqa: E731
    reg = StaticServiceRegistry([rec("a-svc"), rec("b-svc")], requires_registered_credentials=True)
    tok, jti = mint_service_token(cfg, service_id="a-svc", scopes=[ac.INGESTION_PROCESS])
    reg.register_credential(jti, "b-svc")           # registered to someone else
    with pytest.raises(ServiceTokenRejected):
        run(verify_service_token(tok, config=cfg, registry=reg))
    reg.register_credential(jti, "a-svc")
    assert run(verify_service_token(tok, config=cfg, registry=reg)).service_id == "a-svc"
    _ = time
