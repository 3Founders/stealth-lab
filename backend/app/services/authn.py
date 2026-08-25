"""
Identity gate (Band 2.9 / ROADMAP item 9): OIDC-only authentication — real
identity BEFORE multi-user exposure.

What this module is, and is deliberately not:

  IT IS
    - token validation: RS256-only OIDC ID/access tokens verified against a
      JWKS (signature, iss, aud, exp/nbf, required sub). No introspection,
      no session machinery.
    - actor propagation: a validated identity rides a contextvar so service-
      layer write boundaries (events, ChangeSets, review events) can stamp
      WHO without every signature growing an actor parameter.
    - the frozen posture: boot-time refusal of every half-enabled identity
      configuration. Single-tenant public commons stays the only mode until
      OIDC is configured; flipping exposure flags without it cannot boot.
  IT IS NOT
    - user management (no users table writes, no provisioning, no UI).
    - authorization beyond what access.py already does with viewer_id.
    - tenancy filtering (that's HARDENING H1's predicate builder — this
      module deliberately keeps identity ACQUISITION separate from tenancy
      FILTERING so H1 can consume Actors uncoupled).

The spoof this closes: today `get_scope` trusts X-Viewer-Id and
/v1/traces trusts payload `actor_id` — fine while everything is public,
catastrophic the moment private visibility or multi-user exposure turns on.
After this module: a VALIDATED token subject always overrides both; header/
payload identities survive only in the public posture where they grant
nothing world-readable that wasn't already.

WHY A PURE-ASGI MIDDLEWARE, NOT BaseHTTPMiddleware: BaseHTTPMiddleware runs
the downstream app in a separate task; contextvars set before call_next are
inherited by that task, but the indirection has bitten enough asyncio code
that we don't gamble attribution on it. Pure ASGI wraps the downstream app
IN THE SAME TASK, so set/reset of the contextvar brackets the request
exactly, with no framework task plumbing to audit.

JWKS fetching happens via asyncio.to_thread (stdlib urllib): key fetches are
rare (cached, refreshed on unknown kid) but must never block the event loop
— the same discipline 1.8b applied to z3.

Fail-loud rules:
  - a PRESENT but invalid bearer token is always 401 — a bad token must
    never silently degrade into anonymous traffic;
  - a MISSING token is anonymous ONLY in the public posture; once private
    visibility is on, everything except the exemption list (/health,
    OpenAPI docs) requires a valid token;
  - boot refuses: multi-user exposure without OIDC; real_auth_enabled
    without OIDC (half-enabled identity is worse than honest headers);
    private visibility without any real auth (the pre-existing deps.py
    guard, kept intact because its contract is pinned by tests).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

import jwt

# ---------------------------------------------------------------------------
# Actor: the validated identity, and its contextvar carrier.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """A validated OIDC identity. `subject` is the only field attributed
    downstream (actor_id columns); the rest is diagnostic."""

    subject: str
    issuer: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    claims: Mapping[str, Any] = field(default_factory=dict)


_actor_cv: contextvars.ContextVar[Optional[Actor]] = contextvars.ContextVar(
    "current_actor", default=None
)


def set_current_actor(actor: Optional[Actor]) -> "contextvars.Token":
    return _actor_cv.set(actor)


def reset_current_actor(token: "contextvars.Token") -> None:
    _actor_cv.reset(token)


def current_actor() -> Optional[Actor]:
    return _actor_cv.get()


def current_actor_id() -> Optional[str]:
    actor = current_actor()
    return actor.subject if actor else None


# ---------------------------------------------------------------------------
# Configuration + frozen-posture guard.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    allowed_algs: tuple[str, ...] = ("RS256",)

    @classmethod
    def from_settings(cls, settings: Any) -> Optional["OidcConfig"]:
        issuer = getattr(settings, "oidc_issuer", None)
        audience = getattr(settings, "oidc_audience", None)
        if not issuer or not audience:
            return None
        jwks_url = getattr(settings, "oidc_jwks_url", None) or issuer.rstrip("/") + "/.well-known/jwks.json"
        return cls(issuer=issuer, audience=audience, jwks_url=jwks_url)


def oidc_configured(settings: Any) -> bool:
    return OidcConfig.from_settings(settings) is not None


def assert_boot_posture(
    *,
    private_visibility_enabled: bool,
    real_auth_enabled: bool,
    oidc_configured_: bool,
    multi_user_exposure_enabled: bool,
) -> None:
    """Refuse to boot on every half-enabled identity posture.

    The single-tenant PUBLIC commons (all flags off) is the only mode that
    boots without OIDC. Each refusal names the exact lie the flag
    combination would tell, per ROADMAP Band 2.9: the posture must be able
    to silently slip to Band 5 in no direction.

    NOTE: the legacy private-without-real-auth guard lives in
    app/api/deps.py::require_trustworthy_identity and stays there — its
    message text is pinned by test_access.py. This function ADDS the two
    new teeth; main.py calls both at startup.
    """
    if multi_user_exposure_enabled and not oidc_configured_:
        raise RuntimeError(
            "multi_user_exposure_enabled requires OIDC configuration "
            "(OIDC_ISSUER + OIDC_AUDIENCE). Multi-user traffic with "
            "header/payload-asserted identity means anyone can be anyone. "
            "This is the Band 2.9 gate: real identity comes first."
        )
    if real_auth_enabled and not oidc_configured_:
        raise RuntimeError(
            "real_auth_enabled is on, but OIDC is not configured "
            "(set OIDC_ISSUER + OIDC_AUDIENCE). Half-enabled identity is "
            "worse than none: it claims trust the request path cannot "
            "verify."
        )


# ---------------------------------------------------------------------------
# Token validation.
# ---------------------------------------------------------------------------


class TokenRejected(Exception):
    """A presented token failed validation. Message is safe for a 401 body:
    it says WHY in validator terms, never leaking token contents."""


def _b64url_uint(data: bytes) -> int:
    import base64

    pad = "=" * (-len(data) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(data + pad), "big")


def jwk_from_public_numbers(n_bytes: bytes, e_bytes: bytes, kid: str) -> dict[str, Any]:
    """Build a minimal RSA JWK dict from raw big-endian modulus/exponent —
    the offline-test seam (real JWKS responses have exactly these fields)."""

    def _b64u(b: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "kid": kid,
        "n": _b64u(n_bytes),
        "e": _b64u(e_bytes),
        "alg": "RS256",
        "use": "sig",
    }


class StaticJwks:
    """In-memory JWKS source: {kid: jwk_dict}. Tests and offline proofs use
    this; production uses FetchingJwks below."""

    def __init__(self, keys: Mapping[str, Mapping[str, Any]]):
        self._keys = dict(keys)
        self.refresh_calls = 0

    def get_key(self, kid: Optional[str]) -> dict[str, Any]:
        self.refresh_calls += 1  # counts lookups; refresh hook contract below
        if kid is None or kid not in self._keys:
            raise KeyError(kid)
        return dict(self._keys[kid])


class FetchingJwks:
    """JWKS over HTTPS with a TTL cache and one refresh attempt on unknown
    kid (IdPs rotate keys; a cache miss after rotation must recover instead
    of failing until restart). Fetch runs OFF the event loop via
    asyncio.to_thread — rare, but never allowed to block it."""

    def __init__(self, url: str, *, ttl_seconds: float = 300.0):
        self.url = url
        self.ttl_seconds = ttl_seconds
        self._keys: Optional[dict[str, dict[str, Any]]] = None
        self._fetched_at: float = 0.0

    async def get_key(self, kid: Optional[str]) -> dict[str, Any]:
        now = time.monotonic()
        if self._keys is None or now - self._fetched_at > self.ttl_seconds:
            await self._refresh()
        if kid not in (self._keys or {}):
            # One forced refresh before giving up: rotation tolerance.
            await self._refresh()
        try:
            return (self._keys or {})[kid]  # type: ignore[index]
        except KeyError:
            raise KeyError(kid) from None

    async def _refresh(self) -> None:
        def _fetch() -> dict[str, dict[str, Any]]:
            with urllib.request.urlopen(self.url, timeout=10) as resp:  # noqa: S310 - configured URL
                doc = json.loads(resp.read().decode("utf-8"))
            return {k["kid"]: k for k in doc.get("keys", []) if k.get("kty") == "RSA"}

        self._keys = await asyncio.to_thread(_fetch)
        self._fetched_at = time.monotonic()


def validate_token(
    token: str,
    *,
    config: OidcConfig,
    jwks_provider: Any,
) -> Actor:
    """Validate one token against config; returns the Actor or raises
    TokenRejected. Synchronous and network-free given a warm provider —
    callers on the event loop wrap cold fetches themselves (the middleware
    does exactly that)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenRejected(f"unparseable token header: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - garbage bytes must be a 401, not a 500
        raise TokenRejected("unparseable token header") from exc

    alg = header.get("alg")
    if alg not in config.allowed_algs:
        # Checked BEFORE any key material is touched: alg confusion
        # (none / HS256-vs-RS256) is the classic JWT break, and PyJWT will
        # happily verify whatever alg the caller lists — so the whitelist
        # is enforced here, not delegated.
        raise TokenRejected(f"token alg {alg!r} not allowed (allowed: {config.allowed_algs})")

    kid = header.get("kid")
    getter = jwks_provider.get_key
    try:
        maybe_coro = getter(kid)
    except KeyError:
        raise TokenRejected(f"no signing key for kid {kid!r}") from None
    if asyncio.iscoroutine(maybe_coro):
        raise TokenRejected("jwks provider returned a coroutine to sync validate_token")

    try:
        jwk = maybe_coro
    except KeyError:
        raise TokenRejected(f"no signing key for kid {kid!r}") from None

    try:
        key = jwt.PyJWK.from_dict(jwk).key
    except Exception as exc:  # noqa: BLE001 - malformed JWK is a rejection, not a crash
        raise TokenRejected(f"unusable JWK for kid {kid!r}: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(config.allowed_algs),
            audience=config.audience,
            issuer=config.issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.MissingRequiredClaimError as exc:
        raise TokenRejected(f"token missing required claim: {exc}") from exc
    except jwt.ImmatureSignatureError as exc:
        raise TokenRejected("token not yet valid (nbf)") from exc
    except jwt.ExpiredSignatureError as exc:
        raise TokenRejected("token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenRejected("token audience mismatch") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenRejected("token issuer mismatch") from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenRejected("token signature invalid") from exc
    except jwt.PyJWTError as exc:
        raise TokenRejected(f"token rejected: {exc}") from exc

    subject = claims.get("sub")
    if not subject:
        raise TokenRejected("token has empty sub")
    return Actor(
        subject=str(subject),
        issuer=claims.get("iss"),
        email=claims.get("email"),
        name=claims.get("preferred_username") or claims.get("name"),
        claims=dict(claims),
    )


async def validate_token_async(token: str, *, config: OidcConfig, jwks_provider: Any) -> Actor:
    """Async twin: same checks, provider may fetch off-loop."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenRejected(f"unparseable token header: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - garbage bytes must be a 401, not a 500
        raise TokenRejected("unparseable token header") from exc
    alg = header.get("alg")
    if alg not in config.allowed_algs:
        raise TokenRejected(f"token alg {alg!r} not allowed (allowed: {config.allowed_algs})")
    kid = header.get("kid")
    try:
        jwk = jwks_provider.get_key(kid)
    except KeyError:
        raise TokenRejected(f"no signing key for kid {kid!r}") from None
    if asyncio.iscoroutine(jwk):
        jwk = await jwk
    try:
        key = jwt.PyJWK.from_dict(jwk).key
    except Exception as exc:  # noqa: BLE001
        raise TokenRejected(f"unusable JWK for kid {kid!r}: {exc}") from exc
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(config.allowed_algs),
            audience=config.audience,
            issuer=config.issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenRejected(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - garbage bytes must be a 401, not a 500
        raise TokenRejected("unparseable token") from exc
    subject = claims.get("sub")
    if not subject:
        raise TokenRejected("token has empty sub")
    return Actor(
        subject=str(subject),
        issuer=claims.get("iss"),
        email=claims.get("email"),
        name=claims.get("preferred_username") or claims.get("name"),
        claims=dict(claims),
    )


# ---------------------------------------------------------------------------
# Middleware (pure ASGI — see module docstring for why).
# ---------------------------------------------------------------------------

EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def make_actor_middleware(
    config: Optional[OidcConfig],
    jwks_provider: Any,
    *,
    private_visibility_enabled: bool,
):
    """Pure-ASGI middleware factory: validates a presented bearer against
    config, publishes the Actor on the contextvar for the whole downstream
    request, and enforces the missing-token policy implied by the posture.

    Returns the standard wrap(app)->ASGI-callable shape, so it installs via
    add_middleware exactly like a class would.

    With config=None (public posture, OIDC unconfigured) it is an exact
    pass-through: nothing authenticates, nothing blocks, and the posture
    guard at boot is what keeps that posture honest."""

    def wrap(app):  # noqa: ANN001 - ASGI signature

        async def actor_middleware(scope, receive, send):  # noqa: ANN001
            if scope["type"] != "http":
                await app(scope, receive, send)
                return

            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            token = extract_bearer(headers.get("authorization"))
            actor: Optional[Actor] = None

            if config is not None:
                if token is not None:
                    try:
                        actor = await validate_token_async(token, config=config, jwks_provider=jwks_provider)
                    except TokenRejected as exc:
                        body = json.dumps({"detail": f"invalid token: {exc}"}).encode("utf-8")
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 401,
                                "headers": [(b"content-type", b"application/json")],
                            }
                        )
                        await send({"type": "http.response.body", "body": body})
                        return
                elif private_visibility_enabled and scope.get("path") not in EXEMPT_PATHS:
                    body = json.dumps({"detail": "authentication required"}).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return

            tok = set_current_actor(actor)
            try:
                await app(scope, receive, send)
            finally:
                reset_current_actor(tok)

        return actor_middleware

    return wrap


def install_actor_middleware(app: Any, settings: Any, jwks_provider: Any = None) -> None:
    """Wire onto a FastAPI/Starlette app. Idempotent-ish: guards against
    double-install because re-wrapping would validate twice per request."""
    if getattr(app.state, "actor_middleware_installed", False):
        return
    config = OidcConfig.from_settings(settings)
    provider = jwks_provider or (FetchingJwks(config.jwks_url) if config else None)
    app.add_middleware(
        make_actor_middleware(
            config,
            provider,
            private_visibility_enabled=bool(getattr(settings, "private_visibility_enabled", False)),
        )
    )
    app.state.actor_middleware_installed = True


# ---------------------------------------------------------------------------
# Tenancy resolution (HARDENING H1 — CORE-A): Actor -> user row ->
# memberships -> TenantScope.
#
# Deliberately APPENDED, not woven in: everything above validates WHO you
# are; everything below asks WHICH organization's data that entitles you
# to. The two halves stay uncoupled so the predicate builder in
# services/access.py consumes scopes here without this module ever
# building SQL fragments itself — resolution produces the scope object;
# only access.py turns scopes into SQL.
#
# Posture note: zero memberships resolves to the seeded commons
# organization (the V0 default tenant every existing row carries), NOT
# to an error and not to unrestricted. Today's deployment IS the commons;
# refusing membership-less identities would lock out every valid user
# until provisioning exists. When real multi-tenancy goes live, flipping
# zero-memberships to fail-closed is a change to exactly this one
# function — the flip-on-cheap property, same as visibility.
# ---------------------------------------------------------------------------


class IdentityInactive(Exception):
    """The users row for a VALID token is deactivated or expired. A valid
    JWT proves who the caller is, not that they may still act."""


class AmbiguousTenant(Exception):
    """The user holds active memberships in more than one organization.
    Fails LOUD instead of silently picking one — implicit tenant choice
    is exactly the decorative-isolation failure H1 exists to end."""


@dataclass(frozen=True)
class ResolvedMembership:
    organization_id: str
    organization_name: str
    organization_slug: str
    role_name: str


_USER_SELECT = (
    "SELECT id, is_active, t_expired FROM users "
    "WHERE issuer = $1 AND external_subject = $2"
)
_USER_INSERT = (
    "INSERT INTO users (issuer, external_subject, display_name, email) "
    "VALUES ($1, $2, $3, $4) "
    "ON CONFLICT (issuer, external_subject) DO NOTHING RETURNING id"
)
_MEMBERSHIP_SELECT = (
    "SELECT m.organization_id, o.name AS organization_name, "
    "o.slug AS organization_slug, r.name AS role_name "
    "FROM org_memberships m "
    "JOIN organizations o ON o.id = m.organization_id "
    "JOIN roles r ON r.id = m.role_id "
    "WHERE m.user_id = $1::uuid "
    "AND m.t_expired IS NULL AND o.t_expired IS NULL "
    "ORDER BY m.organization_id, r.name"
)


async def ensure_user(pool: Any, actor: Actor) -> str:
    """Get-or-create the users row for a validated Actor; returns its id.

    Creation is explicit here rather than a middleware side effect: the
    first successful login provisions the row, and the INSERT rides
    ON CONFLICT so a concurrent first login cannot double-create.
    Raises IdentityInactive when the row exists but has been
    deactivated — a valid token for a dead account stays dead."""
    row = await pool.fetchrow(_USER_SELECT, actor.issuer or "", actor.subject)
    if row is None:
        inserted = await pool.fetchrow(
            _USER_INSERT,
            actor.issuer or "",
            actor.subject,
            actor.name,
            actor.email,
        )
        if inserted is not None:
            return str(inserted["id"])
        # Lost a create race: the concurrent winner's row is ours to use.
        row = await pool.fetchrow(_USER_SELECT, actor.issuer or "", actor.subject)
        if row is None:
            raise RuntimeError("user row vanished during concurrent provisioning")
    if not row["is_active"] or row["t_expired"] is not None:
        raise IdentityInactive(f"user {actor.subject} is inactive")
    return str(row["id"])


async def resolve_memberships(pool: Any, user_id: str) -> list[ResolvedMembership]:
    """Active memberships of one user, roles joined in."""
    rows = await pool.fetch(_MEMBERSHIP_SELECT, user_id)
    return [
        ResolvedMembership(
            organization_id=str(r["organization_id"]),
            organization_name=r["organization_name"],
            organization_slug=r["organization_slug"],
            role_name=r["role_name"],
        )
        for r in rows
    ]


def tenant_scope_for(memberships: list[ResolvedMembership]) -> "TenantScope":
    """Pure picker over resolved memberships.

    Zero -> the commons organization (posture note above).
    One  -> that organization.
    Many -> AmbiguousTenant naming every candidate: no implicit pick."""
    from app.services.access import TenantScope

    if not memberships:
        return TenantScope.commons()
    orgs = {m.organization_id for m in memberships}
    if len(orgs) > 1:
        detail = ", ".join(
            sorted(f"{m.organization_slug}({m.organization_id})" for m in memberships)
        )
        raise AmbiguousTenant(
            f"active memberships in multiple organizations: {detail}"
        )
    return TenantScope.for_tenant(next(iter(orgs)))


async def tenant_scope_for_actor(pool: Any, actor: Actor) -> tuple[str, TenantScope]:
    """One-call seam for request paths: validated Actor in, (user_id,
    TenantScope) out. This is what deps-style resolvers will call once
    query paths adopt the builder."""
    user_id = await ensure_user(pool, actor)
    memberships = await resolve_memberships(pool, user_id)
    return user_id, tenant_scope_for(memberships)
