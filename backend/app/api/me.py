"""
Personal contributions API (directive Sec 32.4/34, "/me").

Identity comes from the EXACT SAME mechanism `app/api/deps.py::get_scope`
already resolves for every other endpoint -- a validated Band 2.9 OIDC
actor's `.subject` wins when one exists (`app.services.authn.
current_actor()`, published on a contextvar by the actor middleware);
otherwise the trusted-only-because-nothing-is-private-yet `X-Viewer-Id`
header; otherwise anonymous. No second identity path is introduced here.

An anonymous caller (no validated actor, no header) gets a real 401 --
this endpoint never fabricates a personal contribution history for
someone it cannot identify.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user
from app.services.access import AccessScope
from app.services.personal_contributions import get_personal_contributions

router = APIRouter(prefix="/v1/me", tags=["me"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("")
async def get_me(
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    if scope.viewer_id is None:
        raise HTTPException(
            401,
            "no identity for this request -- anonymous callers have no "
            "personal contribution history",
        )
    return await get_personal_contributions(pool, scope.viewer_id, scope=scope)


# --- Phase 6: data rights (LC-006 / LC-007) --------------------------------


@router.get("/export")
async def export_my_data(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Machine-readable export of the caller's own data. Global Commons
    knowledge appears only as publication-action references."""
    from app.services.data_rights import export_user_data

    return await export_user_data(
        pool, subject=principal.subject, actor_user_id=principal.user_id
    )


@router.get("/deletion")
async def preview_my_deletion(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """What a 'delete my data' request would do -- no mutation."""
    from app.services.data_rights import delete_user_data

    return await delete_user_data(pool, subject=principal.subject, dry_run=True)


@router.post("/deletion")
async def request_my_deletion(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Execute deletion: physical-delete private rows with no downstream
    publication, tombstone published sources (history kept, vector cleared),
    retain publication records and independently-sourced global objects.
    Refused if the subject is under legal hold."""
    from app.services.data_rights import delete_user_data

    try:
        return await delete_user_data(
            pool, subject=principal.subject, actor_user_id=principal.user_id,
            dry_run=False,
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc))


# --- Local `.stealth` projects (account-scoped, end-to-end encrypted) ------
#
# Implements docs/local_project_sync_security.md. Surfaces `synced_projects`
# (migration 107/108/109) -- the explicit, durable project_id ->
# owner_subject relationship -- to the authenticated web account.
# `owner_subject` is the SAME validated OIDC/Supabase subject
# `principal.subject` is here. No new identity model for THIS part; the
# sync-device routes below use a DIFFERENT, deliberately separate identity
# (app.services.sync_device_identity) for the local MCP process's own
# ongoing uploads -- see that module's docstring for why.
#
# CIPHERTEXT ONLY: this router never receives or returns plaintext project
# content. `GET .../{project_id}` returns the account's own ciphertext
# blob + the wrapped P-DEK + KDF parameters so the BROWSER can decrypt
# locally; it never returns `files`/`activity` as plaintext (the old,
# superseded plaintext-bootstrap shape). A project only appears here once
# explicitly synced -- an unsynced workspace is invisible to the account,
# by design.


@router.get("/stealth-projects")
async def list_my_stealth_projects(
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Local `.stealth` projects this account has explicitly synced,
    newest sync first. Never another account's projects -- scoped
    strictly to `principal.subject`."""
    from app.stealth.project_sync import list_synced_projects

    rows = await list_synced_projects(pool, owner_subject=principal.subject)
    return {
        "projects": [
            {
                "project_id": str(r["project_id"]),
                "synced_at": r["synced_at"].isoformat() if r["synced_at"] else None,
                "bootstrapped_at": r["bootstrapped_at"].isoformat() if r["bootstrapped_at"] else None,
                "revision": r["revision"],
            }
            for r in rows
        ]
    }


@router.get("/stealth-projects/{project_id}")
async def get_my_stealth_project(
    project_id: str,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """One synced project's CIPHERTEXT + the key material needed to
    decrypt it client-side, for the caller's own subject only. 404 (never
    403) for a project_id that doesn't exist, was never synced, or
    belongs to someone else -- indistinguishable on purpose. Never returns
    plaintext file/activity content -- the server does not have it and
    cannot produce it (see docs/local_project_sync_security.md §D)."""
    import base64
    import uuid as _uuid

    from app.stealth.project_sync import get_synced_project, read_ciphertext

    try:
        _uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(404, "project not found")

    row = await get_synced_project(pool, project_id=project_id, owner_subject=principal.subject)
    if row is None:
        raise HTTPException(404, "project not found")

    ciphertext = await read_ciphertext(row)
    return {
        "project_id": str(row["project_id"]),
        "synced_at": row["synced_at"].isoformat() if row["synced_at"] else None,
        "bootstrapped_at": row["bootstrapped_at"].isoformat() if row["bootstrapped_at"] else None,
        "revision": row["revision"],
        "wrapped_p_dek": row["wrapped_p_dek"],
        "recovery_salt": row["recovery_salt"],
        "kdf_params": row["kdf_params"],
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii") if ciphertext else None,
    }


@router.delete("/stealth-projects/{project_id}")
async def unsync_my_stealth_project(
    project_id: str,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Browser-initiated unsync (the account UI's UNSYNC action) -- the
    same operation the `unsync_local_project` MCP tool performs, reachable
    from the web without a local MCP connection (e.g. to unsync from a
    machine that no longer has the local checkout). Deletes the sync
    relationship + ciphertext + wrapped key, and revokes every live sync
    device credential for this project -- never touches anything local
    (this router has no filesystem access at all). 404 for "never synced"
    and "synced by someone else" alike."""
    import uuid as _uuid

    from app.services.sync_device_identity import revoke_all_sync_device_credentials_for_project
    from app.stealth.project_sync import unsync_project as _unsync_project

    try:
        _uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(404, "project not found")

    deleted = await _unsync_project(pool, project_id=project_id, owner_subject=principal.subject)
    if not deleted:
        raise HTTPException(404, "project not found")

    await revoke_all_sync_device_credentials_for_project(
        pool, project_id=project_id, owner_subject=principal.subject, reason="unsynced_via_web",
    )
    return {"project_id": project_id, "unsynced": True}


# --- Sync device credentials -------------------------------------------
#
# A THIRD, distinct trust domain from both the human Supabase/OIDC session
# above and worker service tokens (app.services.service_identity) -- see
# app.services.sync_device_identity's own module docstring and
# docs/local_project_sync_security.md's Implementation Closure §1. Issued
# only to a browser holding a real Supabase session (this router's own
# `require_authenticated_user`); used afterward by the LOCAL MCP process,
# never the browser, to authenticate ongoing ciphertext uploads without
# needing the browser open.


def _sync_device_config():
    from app.config import settings
    from app.services.sync_device_identity import SyncDeviceTokenConfig

    cfg = SyncDeviceTokenConfig.from_settings(settings)
    if cfg is None:
        raise HTTPException(503, "sync device credentials are not configured on this server")
    return cfg


async def require_sync_device_token(request: Request, pool=Depends(get_pool)):
    """Dependency for the local-process-facing routes below. Deliberately
    NOT `require_authenticated_user` -- a sync device token is a different
    credential type entirely and must never be accepted where a Supabase
    session is expected, or vice versa (see module docstring)."""
    from app.services.authn import extract_bearer
    from app.services.sync_device_identity import SyncDeviceTokenRejected, verify_sync_device_token

    token = extract_bearer(request.headers.get("authorization"))
    if not token:
        raise HTTPException(401, "missing bearer sync device token", headers={"WWW-Authenticate": "Bearer"})
    try:
        return await verify_sync_device_token(token, config=_sync_device_config(), pool=pool)
    except SyncDeviceTokenRejected as exc:
        raise HTTPException(401, f"invalid sync device token: {exc.reason}") from exc


@router.post("/sync-devices")
async def issue_my_sync_device(
    body: dict,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Issue a new sync device credential for `{project_id}`, scoped to
    exactly this account and project. Establishes the sync relationship
    itself first (app.stealth.project_sync.sync_project -- insert-or-
    verify ownership, REFUSED if already synced to someone else) -- this
    is where "the sign-in event establishes the authenticated account
    context, the user's consent step controls whether it's synced"
    (docs/local_project_sync_security.md, Phase 1) actually happens: a
    project is synced the moment its first device credential is issued,
    never merely by the user being signed in."""
    from app.services.sync_device_identity import issue_sync_device_credential
    from app.stealth.project_sync import AlreadySyncedToAnotherAccount, sync_project

    project_id = body.get("project_id") if isinstance(body, dict) else None
    if not project_id or not isinstance(project_id, str):
        raise HTTPException(422, "project_id is required")

    try:
        await sync_project(pool, project_id=project_id, owner_subject=principal.subject)
    except AlreadySyncedToAnotherAccount:
        raise HTTPException(409, "this project is already synced to a different account")

    cfg = _sync_device_config()
    token = await issue_sync_device_credential(pool, cfg, owner_subject=principal.subject, project_id=project_id)
    return {"token": token, "project_id": project_id, "ttl_seconds": cfg.max_ttl_seconds}


@router.post("/sync-devices/rotate")
async def rotate_my_sync_device(
    request: Request,
    pool=Depends(get_pool),
) -> dict:
    """Self-rotation: the local process presents its current, still-valid
    credential as an ordinary Authorization bearer header and receives a
    fresh one for the SAME (owner_subject, project_id). Needs no browser
    involvement -- this is what lets ongoing sync keep working
    indefinitely after the browser closes, as long as rotation happens
    before the current credential expires. `rotate_sync_device_credential`
    does its own full verification of the presented token internally (it
    is not pre-validated by a separate dependency here, so there is a
    single source of truth for "is this token currently valid")."""
    from app.services.authn import extract_bearer
    from app.services.sync_device_identity import SyncDeviceTokenRejected, rotate_sync_device_credential

    token = extract_bearer(request.headers.get("authorization"))
    if not token:
        raise HTTPException(401, "missing bearer sync device token", headers={"WWW-Authenticate": "Bearer"})
    try:
        new_token = await rotate_sync_device_credential(pool, _sync_device_config(), current_token=token)
    except SyncDeviceTokenRejected as exc:
        raise HTTPException(401, f"invalid sync device token: {exc.reason}") from exc
    return {"token": new_token}


@router.delete("/sync-devices/{credential_id}")
async def revoke_my_sync_device(
    credential_id: str,
    body: Optional[dict] = None,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Account-settings action: revoke one sync device credential by id
    (e.g. after a machine is lost). Only the owning account may revoke its
    own credential -- enforced in app.services.sync_device_identity.
    revoke_sync_device_credential's own WHERE clause, never trusted from
    the request."""
    from app.services.sync_device_identity import revoke_sync_device_credential

    reason = (body or {}).get("reason", "revoked_via_web") if isinstance(body, dict) else "revoked_via_web"
    revoked = await revoke_sync_device_credential(
        pool, credential_id=credential_id, owner_subject=principal.subject, reason=str(reason),
    )
    if not revoked:
        raise HTTPException(404, "credential not found")
    return {"credential_id": credential_id, "revoked": True}


# --- Ciphertext upload ---------------------------------------------------


@router.post("/synced-projects/{project_id}/sync")
async def upload_sync_ciphertext(
    project_id: str,
    body: dict,
    pool=Depends(get_pool),
    device: Any = Depends(require_sync_device_token),
) -> dict:
    """Receives ONE opaque ciphertext payload (a full snapshot on first
    sync, or an incremental delta afterward -- this route cannot tell the
    difference and does not need to) for `project_id`, authenticated by a
    sync device credential (never a Supabase session -- this is the route
    the LOCAL PROCESS calls, typically with no browser open at all).

    Body: `{revision: int, ciphertext_base64: str, wrapped_p_dek?: str,
    recovery_salt?: str, kdf_params?: object}`. The token's own
    `project_id` claim must match the path -- a credential issued for one
    project can never upload to another, even if somehow presented there.

    Idempotent: a `revision` not newer than what's already stored is a
    silent no-op (200, not an error) -- see app.stealth.project_sync.
    record_sync_upload's own docstring for why this, not a content hash,
    is the correct idempotency key for ciphertext."""
    import base64

    from app.stealth.project_sync import StaleRevision, record_sync_upload

    if device.project_id != project_id:
        raise HTTPException(403, "this credential is not authorized for this project")

    revision = body.get("revision") if isinstance(body, dict) else None
    ciphertext_b64 = body.get("ciphertext_base64") if isinstance(body, dict) else None
    if not isinstance(revision, int) or not isinstance(ciphertext_b64, str):
        raise HTTPException(422, "revision (int) and ciphertext_base64 (str) are required")
    try:
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "ciphertext_base64 is not valid base64") from exc

    try:
        row = await record_sync_upload(
            pool, project_id=project_id, revision=revision, ciphertext=ciphertext,
            wrapped_p_dek=body.get("wrapped_p_dek"), recovery_salt=body.get("recovery_salt"),
            kdf_params=body.get("kdf_params"),
        )
    except StaleRevision:
        return {"project_id": project_id, "applied": False, "reason": "stale_revision"}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    return {"project_id": project_id, "applied": True, "revision": row["revision"]}
