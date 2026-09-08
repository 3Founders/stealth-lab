"""Hosted workspace registration (launch compliance Phase 3 / LC-001).

A registered workspace is the hosted authorization boundary: repo
execution resolves a caller-named workspace id to its server-side
storage_path here, never trusting a caller-supplied filesystem path.
Registration binds that server-side path, so it is an owner/admin action.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, require_authenticated_user

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


async def _pool(request: Request):
    return request.app.state.pool


class RegisterWorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    storage_path: str = Field(min_length=1, max_length=1024)
    default_branch: str = Field(default="main", max_length=200)


@router.get("")
async def list_workspaces(
    pool=Depends(_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    if not principal.org_ids:
        return {"workspaces": []}
    rows = await pool.fetch(
        "SELECT id::text, name, storage_path, default_branch, t_created "
        "FROM registered_workspaces "
        "WHERE tenant_id = $1::uuid AND t_expired IS NULL ORDER BY name",
        principal.org_ids[0],
    )
    return {"workspaces": [dict(r) for r in rows]}


@router.post("", status_code=201)
async def register(
    body: RegisterWorkspaceBody,
    pool=Depends(_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    from app.services.workspace_registry import (
        WorkspaceNotAuthorized,
        WorkspacePathRejected,
        register_workspace,
    )

    if not principal.org_ids:
        raise HTTPException(403, "workspace registration requires an organization membership")
    if not principal.has_role("owner", "admin"):
        raise HTTPException(403, "only an organization owner or admin may register a workspace")

    try:
        ws = await register_workspace(
            pool,
            actor_subject=principal.subject,
            actor_user_id=principal.user_id,
            tenant_id=principal.org_ids[0],
            actor_role="owner" if principal.has_role("owner") else "admin",
            name=body.name,
            storage_path=body.storage_path,
            default_branch=body.default_branch,
        )
    except WorkspaceNotAuthorized as exc:
        raise HTTPException(403, str(exc))
    except WorkspacePathRejected as exc:
        raise HTTPException(422, str(exc))
    return {
        "id": ws.id, "name": ws.name, "storage_path": ws.storage_path,
        "default_branch": ws.default_branch,
    }
