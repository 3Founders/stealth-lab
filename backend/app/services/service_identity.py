"""
Service identity: how workers and maintenance jobs authenticate.

Workers are NOT users. They present a short-lived signed service credential
(a JWT with its own issuer/audience, pinned algorithm, environment claim and
`jti`), which is verified here into a ServiceAuthContext. Nothing in this
module ever sees or accepts a user token, and a Supabase user token can never
verify here (different issuer/audience/keys) — nor vice versa.

Verification order (all must pass; every failure is one generic rejection so
the caller learns nothing about which check failed beyond a coarse reason):
  1. header alg is exactly the configured alg (never `none`, never a
     different family) and `kid` names a configured key;
  2. signature, iss, aud, exp, iat present and valid; lifetime <= max TTL;
  3. `env` claim == this process's AUTH_ENVIRONMENT (staging credentials
     cannot authenticate to production even if a key were shared);
  4. service_id (`sub`) is registered and not disabled, and registered for
     this environment;
  5. the credential (`jti`) is registered and not revoked (registry backed by
     the database in production);
  6. requested scopes are a SUBSET of the service's registered allowed_scopes —
     a token cannot claim more than the registry grants (escalation is
     rejected, not silently trimmed).

Secrets: only a SHA-256 fingerprint of an issued token is stored; the token is
printed once at issue time and never returned by any API.

Cache: registry lookups are cached for `auth_cache_ttl` seconds (default 30).
That is the maximum time a revocation/disable can take to be observed by a
running verifier; the credential's own `exp` bounds everything else.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Protocol

import jwt

from app.services.auth_context import ALL_SCOPES, ServiceAuthContext

_LEEWAY = 5          # seconds of clock skew tolerated on exp/iat/nbf
_MIN_KEY_BYTES = 32


class ServiceTokenRejected(Exception):
    """Presented service credential failed verification. `reason` is a coarse,
    token-content-free code safe to log and to return."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceTokenConfig:
    issuer: str
    audience: str
    keys: Mapping[str, str] = field(repr=False)      # kid -> secret (HS256) / PEM (asymmetric)
    environment: str = "production"
    alg: str = "HS256"
    max_ttl_seconds: int = 3600

    @classmethod
    def from_settings(cls, settings: Any) -> Optional["ServiceTokenConfig"]:
        raw = getattr(settings, "service_token_keys", None)
        issuer = getattr(settings, "service_token_issuer", None)
        audience = getattr(settings, "service_token_audience", None)
        if not (raw or issuer or audience):
            return None
        if not (raw and issuer and audience):
            raise RuntimeError(
                "SERVICE_TOKEN_ISSUER, SERVICE_TOKEN_AUDIENCE and SERVICE_TOKEN_KEYS must be set "
                "together (half-configured service identity is worse than none)."
            )
        alg = getattr(settings, "service_token_alg", "HS256")
        if alg not in ("HS256", "HS384", "HS512", "EdDSA", "ES256", "RS256"):
            raise RuntimeError(f"unsupported SERVICE_TOKEN_ALG {alg!r}")
        keys = parse_keys(raw)
        if alg.startswith("HS"):
            weak = [k for k, v in keys.items() if len(v.encode()) < _MIN_KEY_BYTES]
            if weak:
                raise RuntimeError(f"SERVICE_TOKEN_KEYS: key(s) {weak} shorter than {_MIN_KEY_BYTES} bytes")
        env = (getattr(settings, "auth_environment", None) or getattr(settings, "environment", "production"))
        return cls(
            issuer=issuer, audience=audience, keys=keys, environment=str(env).strip().lower(),
            alg=alg, max_ttl_seconds=int(getattr(settings, "service_token_max_ttl_seconds", 3600)),
        )


def parse_keys(raw: str) -> dict[str, str]:
    """`kid:secret,kid2:secret2` (secret may contain ':' — split on the first)."""
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        kid, sep, secret = part.partition(":")
        if not sep or not kid or not secret:
            raise RuntimeError("SERVICE_TOKEN_KEYS entries must look like kid:secret")
        out[kid] = secret
    if not out:
        raise RuntimeError("SERVICE_TOKEN_KEYS is empty")
    return out


# ---------------------------------------------------------------------------
# Registry (who exists, what they may hold, which credentials are live)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceRecord:
    service_id: str
    allowed_scopes: frozenset
    roles: frozenset = frozenset()
    environment: str = "production"
    disabled: bool = False


class ServiceRegistry(Protocol):
    requires_registered_credentials: bool

    async def get_service(self, service_id: str) -> Optional[ServiceRecord]: ...
    async def credential_status(self, credential_id: str, service_id: str) -> str: ...   # active|revoked|unknown


class StaticServiceRegistry:
    """In-memory registry for tests and single-process tooling."""

    def __init__(self, services: Iterable[ServiceRecord] = (), *, credentials: Optional[Mapping[str, str]] = None,
                 requires_registered_credentials: bool = False):
        self.services = {s.service_id: s for s in services}
        self.credentials: dict[str, tuple[str, str]] = {}      # jti -> (service_id, status)
        for jti, status in (credentials or {}).items():
            self.credentials[jti] = ("*", status)
        self.revoked: set[str] = set()
        self.requires_registered_credentials = requires_registered_credentials

    def register_credential(self, jti: str, service_id: str) -> None:
        self.credentials[jti] = (service_id, "active")

    def revoke(self, jti: str) -> None:
        self.revoked.add(jti)

    async def get_service(self, service_id: str) -> Optional[ServiceRecord]:
        return self.services.get(service_id)

    async def credential_status(self, credential_id: str, service_id: str) -> str:
        if credential_id in self.revoked:
            return "revoked"
        entry = self.credentials.get(credential_id)
        if entry is None:
            return "unknown"
        sid, status = entry
        if sid not in ("*", service_id):
            return "unknown"
        return status


class PgServiceRegistry:
    """Database-backed registry (service_identities, service_credentials —
    migration 99). Requires every credential to be registered at issue time, so
    a stolen signing key alone cannot mint an accepted token: the jti must also
    exist and be un-revoked. Lookups cached for `ttl` seconds."""

    requires_registered_credentials = True

    def __init__(self, pool: Any, *, ttl: float = 30.0, clock=time.monotonic):
        self.pool, self.ttl, self._clock = pool, ttl, clock
        self._svc: dict[str, tuple[float, Optional[ServiceRecord]]] = {}
        self._cred: dict[tuple[str, str], tuple[float, str]] = {}

    async def get_service(self, service_id: str) -> Optional[ServiceRecord]:
        now = self._clock()
        hit = self._svc.get(service_id)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        row = await self.pool.fetchrow(
            "SELECT service_id, roles, allowed_scopes, environment, disabled_at FROM service_identities WHERE service_id = $1",
            service_id,
        )
        rec = None if row is None else ServiceRecord(
            service_id=row["service_id"], allowed_scopes=frozenset(row["allowed_scopes"] or ()),
            roles=frozenset(row["roles"] or ()), environment=row["environment"], disabled=row["disabled_at"] is not None,
        )
        self._svc[service_id] = (now, rec)
        return rec

    async def credential_status(self, credential_id: str, service_id: str) -> str:
        now = self._clock()
        key = (credential_id, service_id)
        hit = self._cred.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        row = await self.pool.fetchrow(
            "SELECT revoked_at, expires_at FROM service_credentials WHERE credential_id = $1 AND service_id = $2",
            credential_id, service_id,
        )
        if row is None:
            status = "unknown"
        elif row["revoked_at"] is not None:
            status = "revoked"
        else:
            status = "active"
        self._cred[key] = (now, status)
        return status

    def invalidate(self) -> None:
        self._svc.clear()
        self._cred.clear()


# ---------------------------------------------------------------------------
# Mint + verify
# ---------------------------------------------------------------------------


async def _audit(pool: Any, actor: str, action: str, object_id: str, details: dict) -> None:
    """Fail-closed audit row (an unaudited credential change must not happen).
    Details never contain tokens: credential ids (jti) and scopes only."""
    from app.services.audit import record_audit_event

    await record_audit_event(pool, actor_subject=actor, action=action, object_type="service_identity",
                             object_id=object_id, details=details)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_service_token(
    cfg: ServiceTokenConfig,
    *,
    service_id: str,
    scopes: Iterable[str],
    ttl_seconds: int = 900,
    kid: Optional[str] = None,
    jti: Optional[str] = None,
    now: Optional[float] = None,
    environment: Optional[str] = None,
) -> tuple[str, str]:
    """Return (token, jti). The caller registers `jti` (register_credential)
    before the token can verify against a registry that requires it."""
    if ttl_seconds > cfg.max_ttl_seconds:
        raise ValueError(f"ttl {ttl_seconds}s exceeds SERVICE_TOKEN_MAX_TTL_SECONDS={cfg.max_ttl_seconds}")
    kid = kid or next(iter(cfg.keys))
    jti = jti or uuid.uuid4().hex
    iat = int(now if now is not None else time.time())
    claims = {
        "iss": cfg.issuer, "aud": cfg.audience, "sub": service_id, "jti": jti,
        "iat": iat, "nbf": iat, "exp": iat + ttl_seconds,
        "env": (environment or cfg.environment), "scp": sorted(set(scopes)),
    }
    return jwt.encode(claims, cfg.keys[kid], algorithm=cfg.alg, headers={"kid": kid}), jti


async def verify_service_token(
    token: str,
    *,
    config: ServiceTokenConfig,
    registry: ServiceRegistry,
) -> ServiceAuthContext:
    try:
        header = jwt.get_unverified_header(token)
    except Exception:  # noqa: BLE001
        raise ServiceTokenRejected("malformed") from None
    if header.get("alg") != config.alg:
        raise ServiceTokenRejected("bad_alg")
    key = config.keys.get(header.get("kid") or "")
    if key is None:
        raise ServiceTokenRejected("unknown_key")
    try:
        claims = jwt.decode(
            token, key=key, algorithms=[config.alg], audience=config.audience, issuer=config.issuer,
            leeway=_LEEWAY, options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise ServiceTokenRejected("expired") from None
    except jwt.InvalidAudienceError:
        raise ServiceTokenRejected("bad_audience") from None
    except jwt.InvalidIssuerError:
        raise ServiceTokenRejected("bad_issuer") from None
    except jwt.InvalidSignatureError:
        raise ServiceTokenRejected("bad_signature") from None
    except jwt.PyJWTError:
        raise ServiceTokenRejected("invalid") from None

    t = time.time()
    if claims["exp"] - claims["iat"] > config.max_ttl_seconds + _LEEWAY:
        raise ServiceTokenRejected("ttl_too_long")
    if claims["iat"] - _LEEWAY > t:
        raise ServiceTokenRejected("issued_in_future")
    if str(claims.get("env", "")).lower() != config.environment:
        raise ServiceTokenRejected("wrong_environment")

    service_id, jti = str(claims["sub"]), str(claims["jti"])
    rec = await registry.get_service(service_id)
    if rec is None or rec.disabled:
        raise ServiceTokenRejected("unknown_or_disabled_service")
    if rec.environment.lower() != config.environment:
        raise ServiceTokenRejected("wrong_environment")
    status = await registry.credential_status(jti, service_id)
    if status == "revoked" or (status == "unknown" and registry.requires_registered_credentials):
        raise ServiceTokenRejected("credential_revoked_or_unregistered")

    requested = claims.get("scp")
    if not isinstance(requested, list) or not all(isinstance(s, str) for s in requested):
        raise ServiceTokenRejected("bad_scopes")
    requested_set = frozenset(requested)
    if not requested_set <= ALL_SCOPES or not requested_set <= rec.allowed_scopes:
        raise ServiceTokenRejected("scope_not_granted")

    return ServiceAuthContext(
        service_id=service_id, roles=rec.roles, scopes=requested_set, environment=config.environment,
        credential_id=jti, expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Operator helpers (used by the CLI below and by tests)
# ---------------------------------------------------------------------------


async def register_service(pool: Any, *, service_id: str, scopes: Iterable[str], roles: Iterable[str] = (),
                           environment: str, created_by: str) -> None:
    scopes = sorted(set(scopes))
    if not set(scopes) <= ALL_SCOPES:
        raise ValueError(f"unknown scopes: {sorted(set(scopes) - ALL_SCOPES)}")
    await pool.execute(
        "INSERT INTO service_identities (service_id, roles, allowed_scopes, environment, created_by) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (service_id) DO UPDATE SET roles = EXCLUDED.roles, "
        "allowed_scopes = EXCLUDED.allowed_scopes, environment = EXCLUDED.environment, disabled_at = NULL",
        service_id, sorted(set(roles)), scopes, environment, created_by,
    )
    await _audit(pool, created_by, "service.registered", service_id, {"scopes": scopes, "environment": environment})


async def issue_credential(pool: Any, cfg: ServiceTokenConfig, *, service_id: str, scopes: Iterable[str],
                           ttl_seconds: int, created_by: str) -> str:
    token, jti = mint_service_token(cfg, service_id=service_id, scopes=scopes, ttl_seconds=ttl_seconds)
    await pool.execute(
        "INSERT INTO service_credentials (credential_id, service_id, fingerprint, expires_at, created_by) "
        "VALUES ($1, $2, $3, now() + make_interval(secs => $4), $5)",
        jti, service_id, token_fingerprint(token), float(ttl_seconds), created_by,
    )
    await _audit(pool, created_by, "service.credential_issued", service_id, {"credential_id": jti, "ttl_seconds": ttl_seconds})
    return token


async def revoke_credential(pool: Any, *, credential_id: str, reason: str, actor: str = "cli") -> None:
    await _audit(pool, actor, "service.credential_revoked", credential_id, {"reason": reason})
    await pool.execute(
        "UPDATE service_credentials SET revoked_at = now(), revoked_reason = $2 WHERE credential_id = $1 AND revoked_at IS NULL",
        credential_id, reason,
    )


async def disable_service(pool: Any, *, service_id: str, actor: str = "cli") -> None:
    """Break-glass for a leaked worker: disables the identity AND revokes every live credential."""
    await _audit(pool, actor, "service.disabled", service_id, {})
    await pool.execute("UPDATE service_identities SET disabled_at = now() WHERE service_id = $1", service_id)
    await pool.execute(
        "UPDATE service_credentials SET revoked_at = now(), revoked_reason = 'service disabled' "
        "WHERE service_id = $1 AND revoked_at IS NULL", service_id,
    )


def _cli() -> None:  # pragma: no cover - operator tool
    import argparse
    import asyncio

    from app.config import settings
    from app.db.session import create_pool

    ap = argparse.ArgumentParser(description="Manage worker/service identities")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register"); r.add_argument("service_id"); r.add_argument("--scope", action="append", required=True)
    r.add_argument("--role", action="append", default=[])
    m = sub.add_parser("mint"); m.add_argument("service_id"); m.add_argument("--scope", action="append", required=True)
    m.add_argument("--ttl", type=int, default=900)
    rv = sub.add_parser("revoke"); rv.add_argument("credential_id"); rv.add_argument("--reason", required=True)
    d = sub.add_parser("disable"); d.add_argument("service_id")
    a = ap.parse_args()

    async def run() -> None:
        pool = await create_pool()
        cfg = ServiceTokenConfig.from_settings(settings)
        who = "cli"
        env = (settings.auth_environment or settings.environment).lower()
        if a.cmd == "register":
            await register_service(pool, service_id=a.service_id, scopes=a.scope, roles=a.role, environment=env, created_by=who)
        elif a.cmd == "mint":
            if cfg is None:
                raise SystemExit("service token signing is not configured")
            print(await issue_credential(pool, cfg, service_id=a.service_id, scopes=a.scope, ttl_seconds=a.ttl, created_by=who))
        elif a.cmd == "revoke":
            await revoke_credential(pool, credential_id=a.credential_id, reason=a.reason)
        elif a.cmd == "disable":
            await disable_service(pool, service_id=a.service_id)
        await pool.close()

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    _cli()


# ---------------------------------------------------------------------------
# Break-glass: short-lived, reasoned, two-person, audited cross-tenant access.
# There is no permanent universal admin: the only way to hold `tenancy:cross`
# is an unexpired row here, which lapses on its own.
# ---------------------------------------------------------------------------

BREAK_GLASS_MAX_SECONDS = 3600
BREAK_GLASS_MIN_REASON = 20


class BreakGlassDenied(Exception):
    pass


async def grant_break_glass(pool: Any, *, grantor: Any, grantee_user_id: str, reason: str, ttl_seconds: int = 900) -> str:
    """`grantor` must be an AuthContext holding auth:admin and must NOT be the
    grantee (two-person rule). Reason is mandatory and stored; TTL is capped."""
    from app.services import auth_context as ac

    if not isinstance(grantor, ac.AuthContext) or not grantor.has_scope(ac.AUTH_ADMIN):
        raise BreakGlassDenied("grantor must be a human holding auth:admin")
    if grantor.user_id == grantee_user_id:
        raise BreakGlassDenied("two-person rule: you cannot grant break-glass to yourself")
    if len((reason or "").strip()) < BREAK_GLASS_MIN_REASON:
        raise BreakGlassDenied(f"a reason of at least {BREAK_GLASS_MIN_REASON} characters is required")
    if not 0 < ttl_seconds <= BREAK_GLASS_MAX_SECONDS:
        raise BreakGlassDenied(f"ttl must be 1..{BREAK_GLASS_MAX_SECONDS} seconds")
    await _audit(pool, grantor.subject, "break_glass.granted", grantee_user_id, {"reason": reason.strip(), "ttl_seconds": ttl_seconds})
    row = await pool.fetchrow(
        "INSERT INTO break_glass_grants (user_id, granted_by, reason, expires_at) "
        "VALUES ($1::uuid, $2, $3, now() + make_interval(secs => $4)) RETURNING id",
        grantee_user_id, grantor.subject, reason.strip(), float(ttl_seconds))
    return str(row["id"])


async def revoke_break_glass(pool: Any, *, grant_id: str, actor: str) -> None:
    await _audit(pool, actor, "break_glass.revoked", grant_id, {})
    await pool.execute("UPDATE break_glass_grants SET revoked_at = now() WHERE id = $1::bigint AND revoked_at IS NULL", int(grant_id))
