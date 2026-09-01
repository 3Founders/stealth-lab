"""
Read-only Task API (directive Sec 32.4).

Thin FastAPI shell over `app/services/task_api.py::get_task_detail` --
composition logic lives there, same split `app/api/graph.py` establishes
for its own service call. A viewer who cannot see the task_node (or a
task_node that does not exist) gets a plain 404, never a hint either way
-- same anti-enumeration posture `graph.py` already uses.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.execution import implementation_registry
from app.services.access import AccessScope
from app.services.domain_search import search_global
from app.services.task_api import get_task_detail

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("/search")
async def search_tasks(
    q: str,
    repository_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """
    Directive Sec16 convenience endpoint -- a thin, task-only leg of
    `domain_search.search_global` (`object_types=["task"]`), NOT a second
    retrieval engine: same RRF ranking `/v1/search` itself uses for its
    task bucket, reused verbatim. Registered before `/{task_node_id}` for
    readability (the UUID path convertor on that route already rejects
    the literal segment `search` on its own).
    """
    return await search_global(
        pool, q,
        object_types=["task"],
        repository_id=repository_id,
        project_id=project_id,
        limit=limit,
        scope=scope,
    )


@router.get("/{task_node_id}")
async def get_task(
    task_node_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    detail = await get_task_detail(pool, str(task_node_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "task not found")
    return detail


@router.get("/{task_node_id}/implementations")
async def get_task_implementations(
    task_node_id: UUID,
    status: str = Query(
        default="active",
        description=(
            "Lifecycle status to filter to (candidate/active/deprecated/"
            "disabled/quarantined). Pass the sentinel 'all' to see every "
            "linked implementation regardless of lifecycle state -- an "
            "administrative/inspection view, not the default resolution "
            "path (mirrors `implementation_registry.get_for_task`'s own "
            "`status=None` meaning 'every linked implementation')."
        ),
    ),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    """Every implementation linked to this task via `implementation_tasks`,
    visibility-filtered, defaulting to `status='active'` (an ordinary
    retrieval candidate view). Does NOT 404 when the task itself is
    unknown/invisible -- `get_for_task`'s own join against
    `implementation_tasks` honestly returns `[]` for a task with no
    linked implementations, and this endpoint does not independently
    re-check task visibility (no task-existence check exists to leak via
    timing/behavior difference; an empty list is the same honest answer
    either way)."""
    resolved_status = None if status == "all" else status
    if resolved_status is not None and resolved_status not in implementation_registry.STATUS_VALUES:
        raise HTTPException(
            422,
            f"unknown status {resolved_status!r} (valid: "
            f"{implementation_registry.STATUS_VALUES}, or 'all')",
        )
    return await implementation_registry.get_for_task(
        pool, str(task_node_id), scope=scope, status=resolved_status,
    )


@router.post("/{task_node_id}/resolve-implementation")
async def resolve_task_implementation(
    task_node_id: UUID,
    body: dict[str, Any] = Body(default={}),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    """
    Directive Sec 44: "which concrete, durable implementation should
    satisfy this task node?" Body: `{"context": {...}, "constraints":
    {...}}`, both optional -- `context` is accepted for forward
    compatibility (directive's own suggested shape) but is not yet
    consumed by anything (`implementation_registry.resolve()` takes no
    context parameter today; a real router weighing cost/latency/
    capability against `context` is `implementation_executor.py`'s job,
    not this thin read endpoint's). `constraints.hint_kinds`, if given,
    is threaded straight through as `resolve()`'s own `hint_kinds`
    preference-order tuple.

    Always 200, never 404 for "nothing resolved" -- an honest "no active
    implementation is linked to this task" is a real, valid answer to a
    resolution QUESTION (unlike `GET /{id}`, where a missing row
    genuinely doesn't exist). `reason` is a real, honest string, never a
    fabricated justification. Never echoes a credential value -- there is
    none stored to leak (see `implementation_registry.py`'s own
    docstring: `auth_requirements` only ever holds a `credential_ref`
    shape); `requirements`/`invocation` below are the real stored JSONB
    shapes verbatim, nothing synthesized.
    """
    constraints = body.get("constraints") or {}
    hint_kinds_raw = constraints.get("hint_kinds")
    hint_kinds = tuple(hint_kinds_raw) if hint_kinds_raw else None

    resolved = await implementation_registry.resolve(
        pool, str(task_node_id), scope=scope, hint_kinds=hint_kinds,
    )

    if resolved is None:
        reason = (
            "no active implementation is linked to this task"
            if hint_kinds is None else
            f"no active implementation matching hint kinds {list(hint_kinds)} "
            "is linked to this task"
        )
        return {
            "implementation_id": None,
            "provider": None,
            "kind": None,
            "requirements": None,
            "invocation": None,
            "reason": reason,
        }

    reason = (
        "resolved to most recent active implementation, no hint given"
        if hint_kinds is None else
        "resolved via hint preference order"
    )
    return {
        "implementation_id": resolved["id"],
        "provider": resolved["provider"],
        "kind": resolved["kind"],
        "requirements": resolved.get("requirements"),
        "invocation": resolved.get("invocation"),
        "reason": reason,
    }
