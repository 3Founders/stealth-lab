"""Task and implementation registration. curl is the intended UI for these in v1."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import build_services, get_pool
from app.models.plan import ImplementationSpec
from app.services.embeddings import Embedder, task_text, to_pgvector

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: str = Field(default="leaf", pattern="^(leaf|composite)$")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    # Input properties the cache fingerprint is taken over. None = all of
    # them, which is right whenever the input *is* the thing being cached.
    cache_key: Optional[list[str]] = None


class EvalCreate(BaseModel):
    name: str = Field(min_length=1)
    cases: list[dict[str, Any]] = Field(default_factory=list)
    scorer: str = Field(default="subset_match", pattern="^(exact_match|subset_match)$")


def _task_dict(task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "name": task.name,
        "description": task.description,
        "kind": task.kind,
        "input_schema": task.input_schema,
        "output_schema": task.output_schema,
        "success_criteria": task.success_criteria,
        "cache_key": task.cache_key,
        "version": task.version,
    }


@router.get("")
async def list_tasks(
    search: Optional[str] = None, limit: int = 100, pool=Depends(get_pool)
) -> list[dict[str, Any]]:
    services = build_services(pool)
    return [_task_dict(t) for t in await services.graph.list_tasks(search, limit)]


@router.get("/{task_id}")
async def get_task(task_id: UUID, pool=Depends(get_pool)) -> dict[str, Any]:
    services = build_services(pool)
    task = await services.graph.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such task")
    payload = _task_dict(task)
    payload["implementations"] = [
        i.model_dump(mode="json") for i in await services.graph.implementations_for(task_id)
    ]
    if task.kind == "composite":
        payload["expansion"] = [
            _task_dict(c) for c in await services.graph.expansion_of(task_id)
        ]
    return payload


@router.post("")
async def create_task(body: TaskCreate, pool=Depends(get_pool)) -> dict[str, Any]:
    services = build_services(pool)

    embedding = None
    try:
        vector = await Embedder().embed_one(task_text(body.name, body.description))
        embedding = to_pgvector(vector)
    except Exception:  # noqa: BLE001
        # Registering a task must not require an embedding provider. The task
        # is lexically retrievable either way; scripts/seed.py --backfill
        # fills these in later.
        pass

    async with pool.acquire() as conn, conn.transaction():
        task_id = await services.graph.create_task(
            conn,
            name=body.name,
            description=body.description,
            kind=body.kind,
            input_schema=body.input_schema,
            output_schema=body.output_schema,
            success_criteria=body.success_criteria,
            cache_key=body.cache_key,
            embedding=embedding,
        )
    return {"id": str(task_id), "name": body.name}


@router.post("/{task_id}/implementations")
async def add_implementation(
    task_id: UUID, body: ImplementationSpec, pool=Depends(get_pool)
) -> dict[str, Any]:
    services = build_services(pool)
    if await services.graph.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="no such task")

    if body.kind == "python":
        # Fail here rather than at the first execution. An implementation
        # naming a ref that isn't registered is dead weight the router will
        # cheerfully select as the cheapest option.
        from app.runners.registry import REGISTRY

        ref = body.spec.get("ref")
        if ref not in REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"python ref '{ref}' is not registered. "
                f"Available: {', '.join(sorted(REGISTRY))}",
            )

    async with pool.acquire() as conn, conn.transaction():
        impl_id = await services.graph.add_implementation(conn, task_id, body)
    return {"id": str(impl_id), "task_id": str(task_id), "name": body.name}


@router.post("/{task_id}/evals")
async def add_eval(task_id: UUID, body: EvalCreate, pool=Depends(get_pool)) -> dict[str, Any]:
    services = build_services(pool)
    if await services.graph.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="no such task")

    row = await pool.fetchrow(
        """
        INSERT INTO evals (task_node_id, name, cases, scorer)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (task_node_id, name) WHERE t_invalid IS NULL
        DO UPDATE SET cases = EXCLUDED.cases, scorer = EXCLUDED.scorer
        RETURNING id
        """,
        task_id,
        body.name,
        body.cases,
        body.scorer,
    )
    return {"id": str(row["id"]), "task_id": str(task_id), "cases": len(body.cases)}
