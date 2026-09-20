"""
Centralized authorization: "what may this identity do to this object?"

Pure functions over (Principal, ObjectRef). No I/O, no request access, no
database — so REST, MCP and workers all evaluate the SAME policy and the policy
is exhaustively testable. Authentication (services/authn.py, service_identity.py)
runs first and produces the principal; the shard router runs AFTER this and is
never consulted here: `ObjectRef` has no shard field on purpose, so physical
placement cannot influence a decision (tests assert it).

Object scope model (maps the existing `visibility` column):

    visibility 'public'  -> GLOBAL_PUBLIC     anyone reads
    visibility 'private' -> USER_PRIVATE      owner only
    visibility 'org'     -> TENANT_PRIVATE    members of tenant_id
    anything else/unknown-> SYSTEM_INTERNAL   internal services / platform admin only (fail closed)

Rules worth stating once:
  * ADMIN scopes do not imply data access. admin:ops lets you operate the
    platform, not read another tenant's private objects; that needs the
    explicit tenancy:cross scope, which no role grants.
  * A service reaches private data only through the JobAuthority its job was
    submitted with, and only for the owner/tenant on that job.
  * A service may publish a private object only when the job says
    publication_allowed. Workers never decide to publish on their own.
  * Ownership on create comes from the principal. A request that names a
    different owner or tenant is denied, not silently rewritten.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from app.services import auth_context as ac
from app.services.auth_context import AnonymousContext, AuthContext, Principal, ServiceAuthContext, is_expired


class ObjectScope(str, enum.Enum):
    GLOBAL_PUBLIC = "global_public"
    TENANT_PRIVATE = "tenant_private"
    USER_PRIVATE = "user_private"
    SYSTEM_INTERNAL = "system_internal"


_VISIBILITY_TO_SCOPE = {
    "public": ObjectScope.GLOBAL_PUBLIC,
    "private": ObjectScope.USER_PRIVATE,
    "org": ObjectScope.TENANT_PRIVATE,
}


def scope_for_visibility(visibility: Optional[str]) -> ObjectScope:
    return _VISIBILITY_TO_SCOPE.get((visibility or "").strip().lower(), ObjectScope.SYSTEM_INTERNAL)


@dataclass(frozen=True)
class ObjectRef:
    """The authorization-relevant facts of a stored object. `owner_id` is the
    token-subject string stored in owner_id columns; `tenant_id` the org id."""

    visibility: Optional[str]
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    kind: str = "object"          # 'projection' lets projection:write services write derived rows

    @property
    def scope(self) -> ObjectScope:
        return scope_for_visibility(self.visibility)


class Action(str, enum.Enum):
    READ = "read"
    WRITE = "write"
    PUBLISH = "publish"
    EXECUTE = "execute"
    ADMIN = "admin"


class AuthorizationDenied(Exception):
    """`status` is the HTTP semantics: 401 unauthenticated, 403 forbidden,
    404 when the object's existence must not be revealed."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


_TENANT_WRITE_ROLES = frozenset({"owner", "admin", "member"})
_TENANT_ADMIN_ROLES = frozenset({"owner", "admin"})
_INTERNAL_SERVICE_SCOPES = (ac.MAINTENANCE_RUN, ac.PROJECTION_WRITE, ac.INGESTION_PROCESS)


def _tenant_compatible(object_tenant: Optional[str], job_tenant: Optional[str]) -> bool:
    """Either side unset (untenanted) is compatible; otherwise they must match."""
    if object_tenant is None or job_tenant is None:
        return True
    return object_tenant == job_tenant


def _job_covers(ctx: ServiceAuthContext, obj: ObjectRef) -> bool:
    """Does the job this service is bound to authorize touching `obj`?"""
    job = ctx.job
    if job is None:
        return False
    sc = obj.scope
    if sc is ObjectScope.USER_PRIVATE:
        if job.scope != ObjectScope.USER_PRIVATE.value or job.submitted_by_user_id is None:
            return False
        return obj.owner_id == job.submitted_by_user_id and _tenant_compatible(obj.tenant_id, job.tenant_id)
    if sc is ObjectScope.TENANT_PRIVATE:
        if job.scope != ObjectScope.TENANT_PRIVATE.value or job.tenant_id is None:
            return False
        return obj.tenant_id == job.tenant_id
    return False


def _user_can_read(ctx: AuthContext, obj: ObjectRef) -> bool:
    sc = obj.scope
    if sc is ObjectScope.GLOBAL_PUBLIC:
        return True
    if ac.TENANCY_CROSS in ctx.scopes and sc is not ObjectScope.SYSTEM_INTERNAL:
        return True
    if sc is ObjectScope.USER_PRIVATE:
        return obj.owner_id is not None and obj.owner_id == ctx.subject
    if sc is ObjectScope.TENANT_PRIVATE:
        return (obj.tenant_id is not None and obj.tenant_id in ctx.org_ids) or (
            obj.owner_id is not None and obj.owner_id == ctx.subject
        )
    return ac.ADMIN_OPS in ctx.scopes   # SYSTEM_INTERNAL


def can_read(ctx: Principal, obj: ObjectRef) -> bool:
    if is_expired(ctx):
        return False
    sc = obj.scope
    if isinstance(ctx, AnonymousContext):
        return sc is ObjectScope.GLOBAL_PUBLIC
    if isinstance(ctx, ServiceAuthContext):
        if sc is ObjectScope.GLOBAL_PUBLIC:
            return True
        if sc is ObjectScope.SYSTEM_INTERNAL:
            return any(ctx.has_scope(s) for s in _INTERNAL_SERVICE_SCOPES)
        return _job_covers(ctx, obj)
    if isinstance(ctx, AuthContext):
        return _user_can_read(ctx, obj)
    return False


def can_write(ctx: Principal, obj: ObjectRef) -> bool:
    if is_expired(ctx):
        return False
    sc = obj.scope
    if isinstance(ctx, AnonymousContext):
        return False
    if isinstance(ctx, ServiceAuthContext):
        if sc is ObjectScope.SYSTEM_INTERNAL:
            return any(ctx.has_scope(s) for s in _INTERNAL_SERVICE_SCOPES)
        if sc is ObjectScope.GLOBAL_PUBLIC:
            if obj.kind == "projection":
                return ctx.has_scope(ac.PROJECTION_WRITE)
            return (
                ctx.has_scope(ac.INGESTION_PROCESS)
                and ctx.job is not None
                and ctx.job.scope == ObjectScope.GLOBAL_PUBLIC.value
            )
        # private: only under a job that covers the object, with a write-capable scope
        return (ctx.has_scope(ac.INGESTION_PROCESS) or ctx.has_scope(ac.PROJECTION_WRITE)) and _job_covers(ctx, obj)
    if isinstance(ctx, AuthContext):
        if not ctx.has_scope(ac.KNOWLEDGE_WRITE):
            return False
        if sc is ObjectScope.GLOBAL_PUBLIC:
            return ctx.has_scope(ac.KNOWLEDGE_PUBLISH)
        if sc is ObjectScope.USER_PRIVATE:
            # owner_id None == "object being created": the owner will be derived from ctx.
            return obj.owner_id in (None, ctx.subject) and obj.tenant_id in (None, *ctx.org_ids)
        if sc is ObjectScope.TENANT_PRIVATE:
            return obj.tenant_id is not None and bool(ctx.role_in(obj.tenant_id) & _TENANT_WRITE_ROLES)
        return ac.ADMIN_OPS in ctx.scopes
    return False


def can_publish(ctx: Principal, obj: ObjectRef) -> bool:
    """May `ctx` REQUEST that a private object be considered for global
    admission? (Admission itself — verification/sanitization — is a separate
    pipeline gated by knowledge:publish.)"""
    if is_expired(ctx) or isinstance(ctx, AnonymousContext):
        return False
    sc = obj.scope
    if sc not in (ObjectScope.USER_PRIVATE, ObjectScope.TENANT_PRIVATE):
        return False
    if isinstance(ctx, ServiceAuthContext):
        return bool(ctx.job and ctx.job.publication_allowed and _job_covers(ctx, obj))
    if not ctx.has_scope(ac.PUBLICATION_REQUEST):
        return False
    if sc is ObjectScope.USER_PRIVATE:
        return obj.owner_id is not None and obj.owner_id == ctx.subject
    return obj.tenant_id is not None and bool(ctx.role_in(obj.tenant_id) & _TENANT_ADMIN_ROLES)


def can_execute(ctx: Principal, obj: ObjectRef) -> bool:
    if isinstance(ctx, ServiceAuthContext) or isinstance(ctx, AnonymousContext):
        return False   # execution is a user/agent action; workers ingest, they do not execute procedures
    return ctx.has_scope(ac.EXECUTION_RUN) and can_read(ctx, obj)


def can_admin(ctx: Principal, obj: ObjectRef) -> bool:
    if is_expired(ctx) or isinstance(ctx, AnonymousContext):
        return False
    sc = obj.scope
    if isinstance(ctx, ServiceAuthContext):
        return ctx.has_scope(ac.ADMIN_OPS)
    if ctx.has_scope(ac.ADMIN_OPS):
        return True
    if sc is ObjectScope.USER_PRIVATE:
        return obj.owner_id is not None and obj.owner_id == ctx.subject
    if sc is ObjectScope.TENANT_PRIVATE:
        return obj.tenant_id is not None and bool(ctx.role_in(obj.tenant_id) & _TENANT_ADMIN_ROLES)
    return False


_CHECKS = {
    Action.READ: can_read,
    Action.WRITE: can_write,
    Action.PUBLISH: can_publish,
    Action.EXECUTE: can_execute,
    Action.ADMIN: can_admin,
}


def authorize(ctx: Principal, action: Action, obj: ObjectRef, *, hide_existence: bool = False) -> None:
    """Raise AuthorizationDenied unless allowed. With `hide_existence`, a
    denied access to a non-public object is a 404, so a caller cannot tell
    "not yours" from "does not exist"."""
    if _CHECKS[action](ctx, obj):
        return
    if isinstance(ctx, AnonymousContext) and obj.scope is not ObjectScope.GLOBAL_PUBLIC and not hide_existence:
        raise AuthorizationDenied("authentication required", 401)
    if hide_existence and obj.scope is not ObjectScope.GLOBAL_PUBLIC:
        raise AuthorizationDenied("not found", 404)
    raise AuthorizationDenied(f"not permitted to {action.value} this object", 403)


def require_scopes(ctx: Principal, *scopes: str) -> None:
    """All-of scope check. 401 for anonymous, 403 when a scope is missing."""
    if isinstance(ctx, AnonymousContext):
        raise AuthorizationDenied("authentication required", 401)
    if is_expired(ctx):
        raise AuthorizationDenied("credential expired", 401)
    missing = [s for s in scopes if not ctx.has_scope(s)]
    if missing:
        raise AuthorizationDenied("missing required scope: " + ", ".join(sorted(missing)), 403)


def owner_fields_for_create(ctx: Principal, *, visibility: str = "private") -> dict:
    """The ONLY sanctioned source of owner_id / tenant_id / created_by for a new
    object. Request payloads never contribute."""
    if isinstance(ctx, AuthContext):
        return {
            "owner_id": ctx.subject,
            "tenant_id": ctx.tenant_id if visibility == "org" else None,
            "created_by_user_id": ctx.user_id,
            "created_by_service_id": None,
        }
    if isinstance(ctx, ServiceAuthContext):
        job = ctx.job
        return {
            "owner_id": job.submitted_by_user_id if job else None,
            "tenant_id": job.tenant_id if job else None,
            "created_by_user_id": None,
            "created_by_service_id": ctx.service_id,
        }
    raise AuthorizationDenied("authentication required", 401)
