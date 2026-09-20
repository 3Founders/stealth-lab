"""Service identity: worker credential verification (services/service_identity.py).
Offline; no DB. Covers valid / expired / wrong scope / revoked / disabled /
wrong environment / alg confusion / forged / escalation / crossover / rotation."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest

from app.services import auth_context as ac
from app.services.service_identity import (
    PgServiceRegistry, ServiceRecord, ServiceTokenConfig, ServiceTokenRejected, StaticServiceRegistry,
    mint_service_token, parse_keys, token_fingerprint, verify_service_token,
)

K1 = "k1-" + "a" * 40
K2 = "k2-" + "b" * 40
CFG = ServiceTokenConfig(issuer="stealth-svc", audience="stealth-api", keys={"k1": K1}, environment="production")
ING = ServiceRecord("cloud-run-ingestion", frozenset({ac.INGESTION_PROCESS, ac.PROJECTION_WRITE}),
                    frozenset({"ingestion_worker"}), "production")


def run(coro):
    return asyncio.run(coro)


def registry(*extra, **kw):
    return StaticServiceRegistry([ING, *extra], **kw)


def mint(scopes=(ac.INGESTION_PROCESS,), **kw):
    return mint_service_token(CFG, service_id=kw.pop("service_id", "cloud-run-ingestion"), scopes=scopes, **kw)


def reason(token, reg=None, cfg=CFG):
    with pytest.raises(ServiceTokenRejected) as e:
        run(verify_service_token(token, config=cfg, registry=reg or registry()))
    return e.value.reason


def test_valid_credential_yields_a_service_context_with_requested_scopes():
    tok, jti = mint((ac.INGESTION_PROCESS,))
    ctx = run(verify_service_token(tok, config=CFG, registry=registry()))
    assert ctx.service_id == "cloud-run-ingestion" and ctx.scopes == {ac.INGESTION_PROCESS}
    assert ctx.credential_id == jti and ctx.environment == "production" and "ingestion_worker" in ctx.roles
    assert ctx.expires_at is not None
    assert not hasattr(ctx, "user_id"), "a service context has no user identity to impersonate"


def test_expired_credential_rejected():
    tok, _ = mint(now=time.time() - 3600, ttl_seconds=60)
    assert reason(tok) == "expired"


def test_forged_signature_and_tampered_claims_rejected():
    other = ServiceTokenConfig(CFG.issuer, CFG.audience, {"k1": "z" * 40}, "production")
    forged, _ = mint_service_token(other, service_id="cloud-run-ingestion", scopes=[ac.INGESTION_PROCESS], kid="k1")
    assert reason(forged) == "bad_signature"
    tok, _ = mint()
    h, p, s = tok.split(".")
    assert reason(f"{h}.{p[:-2]}xx.{s}") in ("bad_signature", "invalid", "malformed")


def test_algorithm_confusion_rejected():
    now = int(time.time())
    claims = {"iss": CFG.issuer, "aud": CFG.audience, "sub": "cloud-run-ingestion", "jti": "j", "iat": now, "exp": now + 60,
              "env": "production", "scp": [ac.INGESTION_PROCESS]}
    none_tok = jwt.encode(claims, key="", algorithm="none", headers={"kid": "k1"})
    assert reason(none_tok) == "bad_alg"
    hs512 = jwt.encode(claims, K1, algorithm="HS512", headers={"kid": "k1"})
    assert reason(hs512) == "bad_alg"


def test_wrong_issuer_audience_and_missing_required_claims():
    for over in ({"iss": "evil"}, {"aud": "other-api"}):
        now = int(time.time())
        claims = {"iss": CFG.issuer, "aud": CFG.audience, "sub": "cloud-run-ingestion", "jti": "j", "iat": now,
                  "exp": now + 60, "env": "production", "scp": [ac.INGESTION_PROCESS], **over}
        tok = jwt.encode(claims, K1, algorithm="HS256", headers={"kid": "k1"})
        assert reason(tok) in ("bad_issuer", "bad_audience")
    now = int(time.time())
    no_jti = jwt.encode({"iss": CFG.issuer, "aud": CFG.audience, "sub": "cloud-run-ingestion", "iat": now, "exp": now + 60},
                        K1, algorithm="HS256", headers={"kid": "k1"})
    assert reason(no_jti) == "invalid"


def test_environment_binding_blocks_staging_credentials_in_production():
    tok, _ = mint(environment="staging")
    assert reason(tok) == "wrong_environment"
    staging_cfg = ServiceTokenConfig(CFG.issuer, CFG.audience, CFG.keys, "staging")
    assert reason(mint()[0], cfg=staging_cfg) == "wrong_environment"


def test_registry_environment_must_match_too():
    rec = ServiceRecord("cloud-run-ingestion", frozenset({ac.INGESTION_PROCESS}), frozenset(), "staging")
    assert reason(mint()[0], reg=StaticServiceRegistry([rec])) == "wrong_environment"


def test_unknown_disabled_service_rejected():
    tok, _ = mint(service_id="ghost")
    assert reason(tok) == "unknown_or_disabled_service"
    off = ServiceRecord("cloud-run-ingestion", frozenset({ac.INGESTION_PROCESS}), frozenset(), "production", disabled=True)
    assert reason(mint()[0], reg=StaticServiceRegistry([off])) == "unknown_or_disabled_service"


def test_revoked_credential_rejected_and_other_credentials_unaffected():
    reg = registry()
    t1, j1 = mint()
    t2, _ = mint()
    reg.revoke(j1)
    assert reason(t1, reg) == "credential_revoked_or_unregistered"
    assert run(verify_service_token(t2, config=CFG, registry=reg)).service_id == "cloud-run-ingestion"


def test_registered_credentials_mode_rejects_unregistered_jti_even_with_valid_signature():
    """A stolen signing key alone cannot mint an accepted token."""
    reg = registry(requires_registered_credentials=True)
    tok, jti = mint()
    assert reason(tok, reg) == "credential_revoked_or_unregistered"
    reg.register_credential(jti, "cloud-run-ingestion")
    assert run(verify_service_token(tok, config=CFG, registry=reg)).credential_id == jti
    reg.register_credential(jti, "some-other-service")     # bound to a different service id
    assert reason(tok, reg) == "credential_revoked_or_unregistered"


def test_scope_escalation_is_rejected_not_trimmed():
    tok, _ = mint((ac.INGESTION_PROCESS, ac.ADMIN_OPS))            # registry never granted admin:ops
    assert reason(tok) == "scope_not_granted"
    tok, _ = mint(("shards:admin-typo",))
    assert reason(tok) == "scope_not_granted"
    tok, _ = mint((ac.TENANCY_CROSS,))
    assert reason(tok) == "scope_not_granted"


def test_scopes_claim_must_be_a_list_of_strings():
    now = int(time.time())
    claims = {"iss": CFG.issuer, "aud": CFG.audience, "sub": "cloud-run-ingestion", "jti": "j", "iat": now,
              "exp": now + 60, "env": "production", "scp": "ingestion:process"}
    assert reason(jwt.encode(claims, K1, algorithm="HS256", headers={"kid": "k1"})) == "bad_scopes"


def test_ttl_is_capped():
    with pytest.raises(ValueError):
        mint(ttl_seconds=CFG.max_ttl_seconds + 1)
    now = int(time.time())
    claims = {"iss": CFG.issuer, "aud": CFG.audience, "sub": "cloud-run-ingestion", "jti": "j", "iat": now,
              "exp": now + 10 * 3600, "env": "production", "scp": [ac.INGESTION_PROCESS]}
    assert reason(jwt.encode(claims, K1, algorithm="HS256", headers={"kid": "k1"})) == "ttl_too_long"


def test_key_rotation_overlap_and_retirement():
    both = ServiceTokenConfig(CFG.issuer, CFG.audience, {"k1": K1, "k2": K2}, "production")
    old, _ = mint_service_token(both, service_id="cloud-run-ingestion", scopes=[ac.INGESTION_PROCESS], kid="k1")
    new, _ = mint_service_token(both, service_id="cloud-run-ingestion", scopes=[ac.INGESTION_PROCESS], kid="k2")
    for t in (old, new):
        assert run(verify_service_token(t, config=both, registry=registry())).service_id
    only_new = ServiceTokenConfig(CFG.issuer, CFG.audience, {"k2": K2}, "production")
    assert reason(old, cfg=only_new) == "unknown_key"           # retired key stops verifying
    assert run(verify_service_token(new, config=only_new, registry=registry()))


def test_a_human_style_token_never_verifies_as_a_service():
    now = int(time.time())
    user_tok = jwt.encode({"iss": "https://p.supabase.co/auth/v1", "aud": "authenticated", "sub": "uid", "exp": now + 60},
                          "x" * 40, algorithm="HS256")
    assert reason(user_tok) in ("unknown_key", "bad_alg")
    assert reason("not-a-jwt") == "malformed"


def test_reasons_never_contain_token_material():
    tok, _ = mint(scopes=(ac.ADMIN_OPS,))
    r = reason(tok)
    assert tok not in r and K1 not in r


def test_config_from_settings_validation():
    ok = SimpleNamespace(service_token_issuer="i", service_token_audience="a", service_token_keys=f"k1:{K1}",
                         service_token_alg="HS256", service_token_max_ttl_seconds=600, auth_environment="staging", environment="STAGING")
    c = ServiceTokenConfig.from_settings(ok)
    assert c.environment == "staging" and c.max_ttl_seconds == 600 and c.keys["k1"] == K1
    assert ServiceTokenConfig.from_settings(SimpleNamespace(service_token_issuer=None, service_token_audience=None,
                                                             service_token_keys=None)) is None
    with pytest.raises(RuntimeError):                                    # half-configured
        ServiceTokenConfig.from_settings(SimpleNamespace(service_token_issuer="i", service_token_audience=None, service_token_keys=None))
    with pytest.raises(RuntimeError):                                    # weak HS key
        ServiceTokenConfig.from_settings(SimpleNamespace(**{**ok.__dict__, "service_token_keys": "k1:short"}))
    with pytest.raises(RuntimeError):
        parse_keys("nocolon")
    assert parse_keys("a:x:y,b:z") == {"a": "x:y", "b": "z"}


def test_only_a_fingerprint_is_persisted():
    tok, _ = mint()
    fp = token_fingerprint(tok)
    assert len(fp) == 64 and tok not in fp


# ------------------------------------------------------------ PG registry

class _Pool:
    def __init__(self):
        self.svc = {"cloud-run-ingestion": {"service_id": "cloud-run-ingestion", "roles": ["ingestion_worker"],
                                            "allowed_scopes": [ac.INGESTION_PROCESS], "environment": "production", "disabled_at": None}}
        self.cred = {}
        self.calls = 0

    async def fetchrow(self, sql, *a):
        self.calls += 1
        if "FROM service_identities" in sql:
            return self.svc.get(a[0])
        if "FROM service_credentials" in sql:
            return self.cred.get((a[0], a[1]))


def test_pg_registry_requires_registered_credentials_and_caches_with_ttl():
    pool, now = _Pool(), [0.0]
    reg = PgServiceRegistry(pool, ttl=30, clock=lambda: now[0])
    tok, jti = mint()
    assert reason(tok, reg) == "credential_revoked_or_unregistered"          # unknown jti
    pool.cred[(jti, "cloud-run-ingestion")] = {"revoked_at": None, "expires_at": None}
    now[0] += 31                                                              # cache expired -> re-read
    assert run(verify_service_token(tok, config=CFG, registry=reg)).service_id == "cloud-run-ingestion"
    calls = pool.calls
    run(verify_service_token(tok, config=CFG, registry=reg))
    assert pool.calls == calls, "second verification inside the TTL must hit the cache"
    pool.cred[(jti, "cloud-run-ingestion")] = {"revoked_at": "now", "expires_at": None}
    run(verify_service_token(tok, config=CFG, registry=reg))                  # still cached: revocation lag <= TTL
    now[0] += 31
    assert reason(tok, reg) == "credential_revoked_or_unregistered"           # observed after TTL
    reg.invalidate()


def test_pg_registry_disabled_service_rejected_after_ttl():
    pool, now = _Pool(), [0.0]
    reg = PgServiceRegistry(pool, ttl=10, clock=lambda: now[0])
    tok, jti = mint()
    pool.cred[(jti, "cloud-run-ingestion")] = {"revoked_at": None, "expires_at": None}
    assert run(verify_service_token(tok, config=CFG, registry=reg))
    pool.svc["cloud-run-ingestion"]["disabled_at"] = "now"
    now[0] += 11
    assert reason(tok, reg) == "unknown_or_disabled_service"
