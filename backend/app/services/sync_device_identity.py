"""
Sync device identity: how a local keळ MCP process authenticates ongoing
local-project-sync uploads after the browser that started synchronization
has closed.

This is a THIRD, distinct trust domain from both the human Supabase/OIDC
identity (app.services.authn) and worker service credentials
(app.services.service_identity) -- never a user token, never a worker
token, and neither of those can verify here (different issuer/audience/
keys), by construction, same discipline service_identity.py already
documents for itself.

WHY A NEW MODULE, NOT A REUSE OF service_identity.py: `ServiceAuthContext`
has no per-user `owner_subject`, and `require_authenticated_user`
explicitly rejects service credentials ("a user identity is required;
service credentials are not accepted here") -- that rejection is correct
and must stay correct for every OTHER endpoint. A sync device credential
is bound to exactly one (owner_subject, project_id) pair and grants exactly
one scope ("sync:upload") -- narrower and differently-shaped than either
existing identity type, so it gets its own, small, self-contained module
rather than overloading either.

Design reference: docs/local_project_sync_security.md, "Implementation
Closure" section 1.

Secrets: only a SHA-256 fingerprint of an issued token is stored; the token
itself is returned once, at issue/rotate time, and never persisted or
logged.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import asyncpg
import jwt

SYNC_UPLOAD_SCOPE = "sync:upload"
_LEEWAY = 5  # seconds of clock skew tolerated on exp/iat/nbf
_MIN_KEY_BYTES = 32


class SyncDeviceTokenRejected(Exception):
    """Presented sync device credential failed verification. `reason` is a
    coarse, token-content-free code safe to log and to return."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SyncDeviceContext:
    owner_subject: str
    project_id: str
    scope: str
    credential_id: str
    expires_at: datetime


@dataclass(frozen=True)
class SyncDeviceTokenConfig:
    issuer: str
    audience: str
    keys: dict[str, str]
    alg: str = "HS256"
    max_ttl_seconds: int = 2_592_000  # 30 days, matches settings default

    @classmethod
    def from_settings(cls, settings: Any) -> Optional["SyncDeviceTokenConfig"]:
        raw = getattr(settings, "sync_device_token_keys", None)
        issuer = getattr(settings, "sync_device_token_issuer", None)
        audience = getattr(settings, "sync_device_token_audience", None)
        if not (raw or issuer or audience):
            return None
        if not (raw and issuer and audience):
            raise RuntimeError(
                "SYNC_DEVICE_TOKEN_ISSUER, SYNC_DEVICE_TOKEN_AUDIENCE and "
                "SYNC_DEVICE_TOKEN_KEYS must be set together (half-configured "
                "identity is worse than none)."
            )
        alg = getattr(settings, "sync_device_token_alg", "HS256")
        if alg not in ("HS256", "HS384", "HS512", "EdDSA", "ES256", "RS256"):
            raise RuntimeError(f"unsupported SYNC_DEVICE_TOKEN_ALG {alg!r}")
        keys = _parse_keys(raw)
        if alg.startswith("HS"):
            weak = [k for k, v in keys.items() if len(v.encode()) < _MIN_KEY_BYTES]
            if weak:
                raise RuntimeError(f"SYNC_DEVICE_TOKEN_KEYS: key(s) {weak} shorter than {_MIN_KEY_BYTES} bytes")
        return cls(
            issuer=issuer, audience=audience, keys=keys, alg=alg,
            max_ttl_seconds=int(getattr(settings, "sync_device_token_max_ttl_seconds", 2_592_000)),
        )


def _parse_keys(raw: str) -> dict[str, str]:
    """`kid:secret[,kid2:secret2]` -- same grammar service_identity.py's
    parse_keys uses, duplicated rather than imported so this module has no
    dependency on the worker-credential module at all (the two trust
    domains stay structurally independent, not just logically)."""
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        kid, sep, secret = part.partition(":")
        if not sep or not kid or not secret:
            raise RuntimeError("SYNC_DEVICE_TOKEN_KEYS entries must look like kid:secret")
        out[kid] = secret
    if not out:
        raise RuntimeError("SYNC_DEVICE_TOKEN_KEYS is empty")
    return out


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _mint(
    cfg: SyncDeviceTokenConfig, *, owner_subject: str, project_id: str,
    scope: str = SYNC_UPLOAD_SCOPE, ttl_seconds: int, jti: Optional[str] = None,
) -> tuple[str, str]:
    if ttl_seconds > cfg.max_ttl_seconds:
        raise ValueError(f"ttl {ttl_seconds}s exceeds SYNC_DEVICE_TOKEN_MAX_TTL_SECONDS={cfg.max_ttl_seconds}")
    kid = next(iter(cfg.keys))
    jti = jti or uuid.uuid4().hex
    iat = int(time.time())
    claims = {
        "iss": cfg.issuer, "aud": cfg.audience, "sub": owner_subject, "jti": jti,
        "iat": iat, "nbf": iat, "exp": iat + ttl_seconds,
        "project_id": project_id, "scp": [scope],
    }
    return jwt.encode(claims, cfg.keys[kid], algorithm=cfg.alg, headers={"kid": kid}), jti


async def issue_sync_device_credential(
    pool: asyncpg.Pool, cfg: SyncDeviceTokenConfig, *, owner_subject: str, project_id: str,
    ttl_seconds: Optional[int] = None,
) -> str:
    """Mint a fresh credential for (owner_subject, project_id) and register
    it. Only callable from an endpoint that has already verified
    owner_subject via a real Supabase/OIDC session -- this function itself
    trusts its caller completely, same as issue_credential in
    service_identity.py."""
    ttl_seconds = ttl_seconds or cfg.max_ttl_seconds
    token, jti = _mint(cfg, owner_subject=owner_subject, project_id=project_id, ttl_seconds=ttl_seconds)
    await pool.execute(
        "INSERT INTO sync_device_credentials (credential_id, owner_subject, project_id, fingerprint, scope, expires_at) "
        "VALUES ($1, $2, $3::uuid, $4, $5, now() + make_interval(secs => $6))",
        jti, owner_subject, project_id, token_fingerprint(token), SYNC_UPLOAD_SCOPE, float(ttl_seconds),
    )
    return token


async def rotate_sync_device_credential(
    pool: asyncpg.Pool, cfg: SyncDeviceTokenConfig, *, current_token: str,
) -> str:
    """The local process's OWN self-rotation path -- presents its current,
    still-valid credential and receives a fresh one for the SAME
    (owner_subject, project_id), with a new jti and a new expiry. Needs no
    browser involvement, which is the whole point: this is what lets sync
    keep working indefinitely after the browser closes, as long as
    rotation happens before the current credential expires. The old
    credential is revoked immediately (never left live alongside the new
    one) -- one valid credential per device at a time, not an
    accumulating set."""
    ctx = await verify_sync_device_token(current_token, config=cfg, pool=pool)
    new_token, new_jti = _mint(
        cfg, owner_subject=ctx.owner_subject, project_id=ctx.project_id, ttl_seconds=cfg.max_ttl_seconds,
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = 'rotated' "
                "WHERE credential_id = $1 AND revoked_at IS NULL",
                ctx.credential_id,
            )
            await conn.execute(
                "INSERT INTO sync_device_credentials (credential_id, owner_subject, project_id, fingerprint, scope, expires_at) "
                "VALUES ($1, $2, $3::uuid, $4, $5, now() + make_interval(secs => $6))",
                new_jti, ctx.owner_subject, ctx.project_id, token_fingerprint(new_token), SYNC_UPLOAD_SCOPE,
                float(cfg.max_ttl_seconds),
            )
    return new_token


async def revoke_sync_device_credential(
    pool: asyncpg.Pool, *, credential_id: str, owner_subject: str, reason: str,
) -> bool:
    """Only the owning account may revoke its own credential -- the WHERE
    clause enforces this the same way every other ownership check in this
    codebase does (never trusts a caller-supplied owner_subject without
    matching it against the row). Returns True if a live credential was
    actually revoked, False if it didn't exist / wasn't this owner's /
    was already revoked (indistinguishable on purpose, same 404-shaped
    discipline as app/api/me.py)."""
    result = await pool.execute(
        "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
        "WHERE credential_id = $1 AND owner_subject = $2 AND revoked_at IS NULL",
        credential_id, owner_subject, reason,
    )
    return result.endswith(" 1")


async def revoke_all_sync_device_credentials_for_project(
    pool: asyncpg.Pool, *, project_id: str, owner_subject: str, reason: str,
) -> None:
    """Called by unsync (app.stealth.project_sync.unsync_project) -- stops
    all future ongoing sync for that project immediately, independent of
    however many devices hold a live credential for it."""
    await pool.execute(
        "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
        "WHERE project_id = $1::uuid AND owner_subject = $2 AND revoked_at IS NULL",
        project_id, owner_subject, reason,
    )


async def verify_sync_device_token(
    token: str, *, config: SyncDeviceTokenConfig, pool: asyncpg.Pool,
) -> SyncDeviceContext:
    try:
        header = jwt.get_unverified_header(token)
    except Exception:  # noqa: BLE001
        raise SyncDeviceTokenRejected("malformed") from None
    if header.get("alg") != config.alg:
        raise SyncDeviceTokenRejected("bad_alg")
    key = config.keys.get(header.get("kid") or "")
    if key is None:
        raise SyncDeviceTokenRejected("unknown_key")
    try:
        claims = jwt.decode(
            token, key=key, algorithms=[config.alg], audience=config.audience, issuer=config.issuer,
            leeway=_LEEWAY, options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise SyncDeviceTokenRejected("expired") from None
    except jwt.InvalidAudienceError:
        raise SyncDeviceTokenRejected("bad_audience") from None
    except jwt.InvalidIssuerError:
        raise SyncDeviceTokenRejected("bad_issuer") from None
    except jwt.InvalidSignatureError:
        raise SyncDeviceTokenRejected("bad_signature") from None
    except jwt.PyJWTError:
        raise SyncDeviceTokenRejected("invalid") from None

    if claims["exp"] - claims["iat"] > config.max_ttl_seconds + _LEEWAY:
        raise SyncDeviceTokenRejected("ttl_too_long")

    owner_subject, jti = str(claims["sub"]), str(claims["jti"])
    project_id = claims.get("project_id")
    scopes = claims.get("scp")
    if not project_id or not isinstance(scopes, list) or SYNC_UPLOAD_SCOPE not in scopes:
        raise SyncDeviceTokenRejected("malformed_claims")

    # Registry check: the credential must exist, belong to this exact
    # (owner_subject, project_id), and not be revoked. A token that
    # verifies cryptographically but was never registered (or was already
    # revoked) is rejected here -- same "signature alone is not enough"
    # discipline service_identity.py's PgServiceRegistry enforces.
    row = await pool.fetchrow(
        "SELECT revoked_at, expires_at FROM sync_device_credentials "
        "WHERE credential_id = $1 AND owner_subject = $2 AND project_id = $3::uuid",
        jti, owner_subject, project_id,
    )
    if row is None:
        raise SyncDeviceTokenRejected("unregistered")
    if row["revoked_at"] is not None:
        raise SyncDeviceTokenRejected("revoked")

    return SyncDeviceContext(
        owner_subject=owner_subject, project_id=project_id, scope=SYNC_UPLOAD_SCOPE,
        credential_id=jti, expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
    )
