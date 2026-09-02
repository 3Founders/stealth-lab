"""
Durable execution-run retry / resume REST surface (final-V1 §2, §34).

Thin: every route hydrates scope and delegates to
`app.execution.durable_resume` (mutations) or `app.execution.durable_run`
(reads). NO retry / resume / scheduling logic here -- that all lives in
the proven durable-run service; this router only exposes it.

  GET  /v1/runs/{run_id}                       -- status + per-node attempt state
  GET  /v1/runs/{run_id}/nodes                 -- full per-node attempt history
  POST /v1/runs/{run_id}/resume                -- resume an eligible run
  POST /v1/runs/{run_id}/nodes/{node_order}/retry?force=  -- retry one node

Authorization (§2/§9): reads are open (`AccessScope.unrestricted()` posture,
same as every other reader). Mutations are gated on the resolved caller
identity (`scope.viewer_id`) matching `execution_runs.created_by` when both
are known; a resolvable, different identity gets 403.
"""
from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.execution import durable_resume as _dres
from app.execution import durable_run as _dr
from app.services.access import AccessScope
from app.utils.ids import uuid7

router = APIRouter(prefix="/v1/runs", tags=["execution-runs"])


async def get_pool(request: Request):
    return request.app.state.pool


def _worker_id() -> str:
    return f"rest-{uuid7().hex[:8]}"


@router.get("/{run_id}")
async def get_run(run_id: str, pool=Depends(get_pool),
                  scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    status = await _dres.run_status_by_id(pool, run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="execution run not found")
    return status


@router.get("/{run_id}/nodes")
async def get_run_nodes(run_id: str, pool=Depends(get_pool),
                        scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    hist = await _dres.node_history_by_id(pool, run_id)
    if hist is None:
        raise HTTPException(status_code=404, detail="execution run not found")
    return hist


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, pool=Depends(get_pool),
                     scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await _dres.resume_run_by_id(
            pool, run_id, worker_id=_worker_id(), actor_id=scope.viewer_id,
        )
    except _dres.NotYourRun as e:
        raise HTTPException(status_code=403, detail=str(e))
    except _dr.ResumeInProgress as e:
        raise HTTPException(status_code=409, detail=str(e))
    except _dr.DurableRunError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except asyncpg.PostgresError as e:
        # e.g. trg_ern_terminal_fence rejecting a stale mutation -- surface
        # the DB's own message, never swallow it into a fake success.
        raise HTTPException(status_code=409, detail=f"database rejected the transition: {e}")


@router.post("/{run_id}/nodes/{node_order}/retry")
async def retry_node(run_id: str, node_order: int, force: bool = Query(False),
                     pool=Depends(get_pool),
                     scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    try:
        return await _dres.retry_run_node_by_id(
            pool, run_id, node_order,
            worker_id=_worker_id(), actor_id=scope.viewer_id, force=force,
        )
    except _dres.NotYourRun as e:
        raise HTTPException(status_code=403, detail=str(e))
    except _dr.ResumeInProgress as e:
        raise HTTPException(status_code=409, detail=str(e))
    except _dr.DurableRunError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except asyncpg.PostgresError as e:
        raise HTTPException(status_code=409, detail=f"database rejected the transition: {e}")
