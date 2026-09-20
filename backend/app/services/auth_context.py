"""
The canonical authenticated identity objects.

Authentication answers "who is this?" and produces exactly one of:

  AuthContext         a human, verified from a Supabase/OIDC access token and
                      resolved against the users / org_memberships /
                      platform_role_grants tables ONCE per request;
  ServiceAuthContext  a worker/maintenance service, verified from a signed
                      service credential (services/service_identity.py);
  AnonymousContext    no credential presented (public commons only).

They are deliberately three types, not one with a nullable user: a service can
never be mistaken for a user (and never inherits a user's private data by
"supplying a user_id"), and authorization (services/authorization.py) can state
per-type policy explicitly.

Nothing here reads request bodies. tenant / owner identifiers come from the
verified token + membership rows only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, FrozenSet, Mapping, Optional, Union

from app.services.access import AccessScope

# ---------------------------------------------------------------------------
# Scopes. Explicit strings; roles only *map* to them (ROLE_SCOPES below).
# ---------------------------------------------------------------------------

KNOWLEDGE_READ = "knowledge:read"
KNOWLEDGE_WRITE = "knowledge:write"          # create/modify OWN (or own-tenant) objects
KNOWLEDGE_PUBLISH = "knowledge:publish"      # global admission / approve / promote
PUBLICATION_REQUEST = "publication:request"  # ask to publish an object you own
RETRIEVAL_READ = "retrieval:read"
EXECUTION_RUN = "execution:run"
EXECUTION_READ = "execution:read"
INGESTION_SUBMIT = "ingestion:submit"
INGESTION_PROCESS = "ingestion:process"
PROJECTION_WRITE = "projection:write"
MAINTENANCE_RUN = "maintenance:run"
ADMIN_OPS = "admin:ops"
SHARDS_ADMIN = "shards:admin"
AUTH_ADMIN = "auth:admin"
TENANCY_CROSS = "tenancy:cross"              # explicit cross-tenant read; never granted by any role

ALL_SCOPES: FrozenSet[str] = frozenset({
    KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_PUBLISH, PUBLICATION_REQUEST, RETRIEVAL_READ,
    EXECUTION_RUN, EXECUTION_READ, INGESTION_SUBMIT, INGESTION_PROCESS, PROJECTION_WRITE,
    MAINTENANCE_RUN, ADMIN_OPS, SHARDS_ADMIN, AUTH_ADMIN, TENANCY_CROSS,
})

# Every active human. Writes are to their OWN private scope; the object-level
# policy (authorization.py) is what stops them touching anyone else's.
USER_BASELINE_SCOPES: FrozenSet[str] = frozenset({
    KNOWLEDGE_READ, KNOWLEDGE_WRITE, PUBLICATION_REQUEST, RETRIEVAL_READ,
    EXECUTION_RUN, EXECUTION_READ, INGESTION_SUBMIT,
})

# Platform roles come from the platform_role_grants table (revocable, audited),
# never from token claims a user could influence and never from org roles: an
# organization's "admin" is not a platform admin.
ROLE_SCOPES: Mapping[str, FrozenSet[str]] = {
    "user": USER_BASELINE_SCOPES,
    "reviewer": frozenset({KNOWLEDGE_PUBLISH}),
    "platform_admin": frozenset({
        KNOWLEDGE_PUBLISH, ADMIN_OPS, SHARDS_ADMIN, AUTH_ADMIN, MAINTENANCE_RUN, PROJECTION_WRITE,
    }),
}

# Service roles (registered per service in service_identities.roles). A token's
# scopes are further intersected with the registry's allowed_scopes.
SERVICE_ROLE_SCOPES: Mapping[str, FrozenSet[str]] = {
    "ingestion_worker": frozenset({INGESTION_PROCESS, PROJECTION_WRITE}),
    "projection_worker": frozenset({PROJECTION_WRITE}),
    "reindex_worker": frozenset({PROJECTION_WRITE, MAINTENANCE_RUN}),
    "maintenance_worker": frozenset({MAINTENANCE_RUN, PROJECTION_WRITE}),
}

# The complete list of endpoints that are intentionally reachable without a
# credential. Everything else is authenticated-by-default (or public-rows-only
# for the read routes that resolve to AccessScope.anonymous()).
PUBLIC_PATHS: FrozenSet[str] = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def scopes_for_roles(roles: Any) -> FrozenSet[str]:
    out: set[str] = set()
    for r in roles:
        out |= ROLE_SCOPES.get(r, frozenset())
    return frozenset(out)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Human
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthContext:
    """A verified human. `user_id` is users.id (provisioned on first login);
    `subject` is the verified token `sub` (Supabase auth.users uid) and is the
    value stored in owner_id / actor columns. `org_ids` are ACTIVE memberships
    at the moment of the request; `tenant_id`/`org_id` is set only when there is
    exactly one (multi-org callers must not have a tenant picked for them)."""

    user_id: str
    subject: str
    tenant_id: Optional[str] = None
    org_id: Optional[str] = None
    roles: FrozenSet[str] = frozenset()          # org role names across memberships (owner/admin/member/viewer)
    scopes: FrozenSet[str] = USER_BASELINE_SCOPES
    auth_method: str = "supabase_jwt"
    session_id: Optional[str] = None
    token_id: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    issuer: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    org_ids: tuple[str, ...] = ()
    org_roles: Mapping[str, FrozenSet[str]] = field(default_factory=dict)
    platform_roles: FrozenSet[str] = frozenset()
    break_glass: bool = False
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    kind = "user"

    def has_role(self, *names: str) -> bool:
        return any(r in self.roles for r in names)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def role_in(self, org_id: str) -> FrozenSet[str]:
        return frozenset(self.org_roles.get(str(org_id), frozenset()))

    def access_scope(self) -> AccessScope:
        """The SQL visibility scope: public rows, own rows and (when member)
        their organizations' org-visibility rows. Derived, never stored, so it
        cannot drift from the identity it came from (data-flow spec INV-02)."""
        if self.org_ids:
            return AccessScope.for_org_member(self.subject, list(self.org_ids))
        return AccessScope.for_user(self.subject)

    @property
    def actor_id(self) -> str:
        return self.subject

    @property
    def rate_key(self) -> str:
        return f"viewer:{self.subject}"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobAuthority:
    """Server-created, immutable authorization carried by an ingestion job.
    A worker can act on a private object ONLY through the authority its job
    was submitted with — it cannot widen it by supplying a user_id."""

    submitted_by_user_id: Optional[str] = None      # users.id / token subject of the submitter
    submitted_by_service_id: Optional[str] = None
    tenant_id: Optional[str] = None
    scope: str = "user_private"                     # ObjectScope value
    visibility: str = "private"
    source_access_scope: Optional[str] = None
    publication_allowed: bool = False


@dataclass(frozen=True)
class ServiceAuthContext:
    service_id: str
    roles: FrozenSet[str] = frozenset()
    scopes: FrozenSet[str] = frozenset()
    environment: str = "production"
    credential_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    auth_method: str = "service_token"
    job: Optional[JobAuthority] = None

    kind = "service"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_role(self, *names: str) -> bool:
        return any(r in self.roles for r in names)

    def bind_job(self, job: JobAuthority) -> "ServiceAuthContext":
        """The same service, now acting under one job's authority."""
        return ServiceAuthContext(
            self.service_id, self.roles, self.scopes, self.environment,
            self.credential_id, self.expires_at, self.auth_method, job,
        )

    def access_scope(self) -> AccessScope:
        # A service reading through the request layer sees PUBLIC rows only.
        # Private data is reachable solely via bind_job() + authorization.py.
        return AccessScope.anonymous()

    @property
    def actor_id(self) -> str:
        return f"service:{self.service_id}"

    @property
    def rate_key(self) -> str:
        return f"service:{self.service_id}"


@dataclass(frozen=True)
class AnonymousContext:
    scopes: FrozenSet[str] = frozenset()
    auth_method: str = "anonymous"
    kind = "anonymous"

    def has_scope(self, scope: str) -> bool:  # noqa: ARG002
        return False

    def has_role(self, *names: str) -> bool:  # noqa: ARG002
        return False

    def access_scope(self) -> AccessScope:
        return AccessScope.anonymous()

    @property
    def actor_id(self) -> None:
        return None


Principal = Union[AuthContext, ServiceAuthContext, AnonymousContext]


def is_expired(ctx: Principal, now: Optional[datetime] = None) -> bool:
    exp = _aware(getattr(ctx, "expires_at", None))
    return exp is not None and exp <= (now or datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Resolution (the one place a verified Actor becomes an AuthContext)
# ---------------------------------------------------------------------------

_PLATFORM_ROLES_SELECT = (
    "SELECT role FROM platform_role_grants "
    "WHERE user_id = $1::uuid AND t_expired IS NULL"
)
_BREAK_GLASS_SELECT = (
    "SELECT 1 FROM break_glass_grants WHERE user_id = $1::uuid AND revoked_at IS NULL AND expires_at > now() LIMIT 1"
)
_MEMBERSHIP_ROLE_SELECT = (
    "SELECT m.organization_id, r.name AS role_name FROM org_memberships m "
    "JOIN roles r ON r.id = m.role_id "
    "JOIN organizations o ON o.id = m.organization_id "
    "WHERE m.user_id = $1::uuid AND m.t_expired IS NULL AND o.t_expired IS NULL"
)


def _claim_time(claims: Mapping[str, Any], name: str) -> Optional[datetime]:
    v = claims.get(name)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return None


async def resolve_auth_context(pool: Any, actor: Any, *, strict_tenant: bool = False) -> AuthContext:
    """Verified Actor -> AuthContext. Raises authn.IdentityInactive for a valid
    token whose account is deactivated (callers turn that into 403 — it is NEVER
    swallowed into a narrower-but-live scope), and authn.AmbiguousTenant only
    when `strict_tenant` (write paths that need a single tenant)."""
    from app.services.authn import ensure_user, resolve_memberships

    user_id = await ensure_user(pool, actor)
    memberships = await resolve_memberships(pool, user_id)
    org_ids = tuple(sorted({m.organization_id for m in memberships}))
    org_roles: dict[str, set[str]] = {}
    for m in memberships:
        org_roles.setdefault(m.organization_id, set()).add(m.role_name)
    if strict_tenant and len(org_ids) > 1:
        from app.services.authn import AmbiguousTenant

        raise AmbiguousTenant(
            "active memberships in multiple organizations: " + ", ".join(sorted(org_ids))
        )

    platform_roles: set[str] = set()
    try:
        rows = await pool.fetch(_PLATFORM_ROLES_SELECT, user_id)
        platform_roles = {r["role"] for r in rows}
    except Exception as exc:  # noqa: BLE001 — a missing table/transient error can only REMOVE privilege
        import logging

        logging.getLogger(__name__).warning("platform role lookup failed (%s); no platform roles granted", type(exc).__name__)

    scopes = scopes_for_roles({"user", *platform_roles})
    break_glass = False
    try:
        if await pool.fetchrow(_BREAK_GLASS_SELECT, user_id) is not None:
            scopes = scopes | {TENANCY_CROSS}     # short-lived, reasoned, audited grant; expires by itself
            break_glass = True
    except Exception:  # noqa: BLE001 - failure can only REMOVE privilege
        pass
    single = org_ids[0] if len(org_ids) == 1 else None
    claims = dict(getattr(actor, "claims", {}) or {})
    return AuthContext(
        user_id=user_id,
        subject=actor.subject,
        tenant_id=single,
        org_id=single,
        roles=frozenset(m.role_name for m in memberships),
        scopes=scopes,
        auth_method="supabase_jwt" if "supabase" in str(getattr(actor, "issuer", "")).lower() or "/auth/v1" in str(getattr(actor, "issuer", "")) else "oidc_jwt",
        session_id=claims.get("session_id"),
        token_id=claims.get("jti"),
        issued_at=_claim_time(claims, "iat"),
        expires_at=_claim_time(claims, "exp"),
        issuer=actor.issuer,
        email=actor.email,
        name=actor.name,
        org_ids=org_ids,
        org_roles={k: frozenset(v) for k, v in org_roles.items()},
        platform_roles=frozenset(platform_roles),
        break_glass=break_glass,
        claims=claims,
    )
