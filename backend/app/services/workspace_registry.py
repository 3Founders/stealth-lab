"""
Phase 1 P0 (launch compliance spec V1): the hosted repository/workspace
authorization boundary.

THE BOUNDARY, STATED PLAINLY: a caller-controlled filesystem path must
never be the authorization mechanism for hosted execution. In hosted mode
(settings.hosted_execution_enabled), every repo execution resolves a
caller-named workspace id to its server-side storage_path from the
registered_workspaces registry (db/41) — and only after confirming the
caller's tenant owns that workspace. The caller names WHAT to execute
against; the server decides WHERE it lives.

Local/loopback mode (the default) is intentionally unchanged: repo_path
remains accepted directly, documented as the local dev posture. The split
is mode-based, not caller-based — the same code path serves both, and
hosted mode simply refuses anything that is not a registered workspace
root. Traversal/symlink rejection in RepoSandbox remains as defense in
depth UNDER this boundary, not instead of it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


class WorkspaceNotAuthorized(PermissionError):
    """The caller may not execute against (or register) the named workspace."""


class WorkspaceNotFound(LookupError):
    """No active workspace with that id (or it belongs to another tenant)."""


# Roles allowed to register a workspace root for a tenant. Registration
# binds a SERVER-SIDE filesystem path, so it is an operator/admin action,
# never something a plain member can do.
_REGISTRAR_ROLES = frozenset({"owner", "admin"})

# A registered root must live under one of these prefixes -- a hard
# backstop so an authorized-but-mistaken admin cannot bind '/etc' or a
# home directory as a workspace. Override via settings.workspace_root_allowlist.
_DEFAULT_ROOT_ALLOWLIST = ("/srv/workspaces", "/workspaces", "/data/workspaces")


class WorkspacePathRejected(ValueError):
    """The proposed storage_path is not an acceptable server-side root."""


def _canonical_root(path: str, allowlist: tuple[str, ...]) -> str:
    """Resolve to an absolute, symlink-free, traversal-free path and
    confirm it sits under an allowed prefix."""
    real = os.path.realpath(os.path.abspath(path))
    norm = os.path.normpath(real)
    if ".." in norm.split(os.sep):
        raise WorkspacePathRejected(f"path traversal in {path!r}")
    allowed = tuple(os.path.normpath(p) for p in allowlist)
    if not any(norm == a or norm.startswith(a + os.sep) for a in allowed):
        raise WorkspacePathRejected(
            f"{norm!r} is not under an allowed workspace root ({', '.join(allowed)})"
        )
    return norm


_WS_INSERT = """
    INSERT INTO registered_workspaces (tenant_id, name, storage_path, default_branch, created_by)
    VALUES ($1::uuid, $2, $3, $4, $5)
    RETURNING id::text, tenant_id::text, name, storage_path, default_branch
"""


async def register_workspace(
    pool: Any,
    *,
    actor_subject: str,
    actor_user_id: Optional[str],
    tenant_id: str,
    actor_role: str,
    name: str,
    storage_path: str,
    default_branch: str = "main",
    root_allowlist: Optional[tuple[str, ...]] = None,
) -> "RegisteredWorkspace":
    """Register a hosted workspace root for a tenant. Requires an
    owner/admin role in that tenant; canonicalizes + allowlists the path;
    emits a `workspace_registered` audit event."""
    if actor_role not in _REGISTRAR_ROLES:
        raise WorkspaceNotAuthorized(
            f"role {actor_role!r} may not register a workspace (need owner/admin)"
        )
    root = _canonical_root(storage_path, root_allowlist or _DEFAULT_ROOT_ALLOWLIST)
    row = await pool.fetchrow(_WS_INSERT, tenant_id, name, root, default_branch, actor_subject)

    try:
        from app.services.audit import record_audit_event

        await record_audit_event(
            pool, actor_subject=actor_subject, action="workspace_registered",
            object_type="registered_workspace", object_id=row["id"],
            actor_user_id=actor_user_id, tenant_id=tenant_id,
            details={"name": name, "storage_path": root, "default_branch": default_branch},
        )
    except Exception:  # noqa: BLE001 - audit best-effort; the row is already durable
        pass

    return RegisteredWorkspace(
        id=row["id"], tenant_id=row["tenant_id"], name=row["name"],
        storage_path=row["storage_path"], default_branch=row["default_branch"],
    )


@dataclass(frozen=True)
class RegisteredWorkspace:
    id: str
    tenant_id: str
    name: str
    storage_path: str
    default_branch: str


_WORKSPACE_FETCH = (
    "SELECT id::text, tenant_id::text, name, storage_path, default_branch "
    "FROM registered_workspaces "
    "WHERE id = $1::uuid AND t_expired IS NULL"
)


async def resolve_workspace_for_actor(
    pool: Any,
    *,
    workspace_id: str,
    actor_tenant_id: str,
) -> RegisteredWorkspace:
    """Resolve workspace_id -> server-side storage_path, authorized.

    Authorization is TENANT OWNERSHIP: the workspace row must exist, be
    active, and belong to the actor's tenant. NotFound is deliberately
    returned for a foreign-tenant workspace (not Forbidden) — distinguishing
    them would leak the existence of other tenants' workspaces.
    """
    row = await pool.fetchrow(_WORKSPACE_FETCH, workspace_id)
    if row is None or row["tenant_id"] != str(actor_tenant_id):
        raise WorkspaceNotFound(
            f"no active workspace {workspace_id!r} for this tenant"
        )
    return RegisteredWorkspace(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        storage_path=row["storage_path"],
        default_branch=row["default_branch"],
    )


def enforce_hosted_repo_path(
    *,
    settings: Any,
    repo_path: Optional[str],
    workspace: Optional[RegisteredWorkspace],
) -> str:
    """Return the ONLY filesystem path hosted execution may use.

    Hosted mode: repo_path, if the caller supplied one at all, must equal
    the registered workspace's server-side storage_path (normalized) — a
    caller cannot redirect execution elsewhere by naming a different path.
    The returned path is the registry's, never the caller's string.

    Local mode: repo_path passes through unchanged (documented dev posture).
    """
    if not getattr(settings, "hosted_execution_enabled", False):
        if repo_path is None:
            raise WorkspaceNotFound("repo_path is required for local execution")
        return repo_path

    if workspace is None:
        raise WorkspaceNotAuthorized(
            "hosted execution requires a registered workspace id; "
            "caller-supplied filesystem paths are not an authorization mechanism"
        )
    if repo_path is not None:
        requested = os.path.normpath(os.path.abspath(repo_path))
        registered = os.path.normpath(os.path.abspath(workspace.storage_path))
        if requested != registered:
            raise WorkspaceNotAuthorized(
                "repo_path does not match the registered workspace root"
            )
    return workspace.storage_path
