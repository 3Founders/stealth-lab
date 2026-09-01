"""
Read-only Task API (directive Sec 32.4).

Thin FastAPI shell over `app/services/task_api.py::get_task_detail` --
composition logic lives there, same split `app/api/graph.py` establishes
for its own service call. A viewer who cannot see the task_node (or a
task_node that does not exist) gets a plain 404, never a hint either way
-- same anti-enumeration posture `graph.py` already uses.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_scope
from app.services.access import AccessScope
from app.services.task_api import get_task_detail

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


async def get_pool(request: Request):
    return request.app.state.pool


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
