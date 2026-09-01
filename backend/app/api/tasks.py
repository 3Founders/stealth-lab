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

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
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
