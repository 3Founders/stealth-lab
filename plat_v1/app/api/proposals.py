"""
The approval gate.

A proposal that failed typecheck is stored with its problems and is **not
approvable** -- the approve endpoint refuses it. Structural failure should
never reach a human as something they can wave through: a reviewer looking at
a plausible-looking DAG has no way to see that node 4's input is produced by
nothing, and a UI that offers an approve button implies someone has already
decided it is a judgement call.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import build_services, get_pool
from app.models.plan import Plan
from app.services import runs as run_store
from app.services.typecheck import typecheck, typecheck_report

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/proposals", tags=["proposals"])


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    decided_by: str = "operator"


def _row_to_dict(row) -> dict[str, Any]:
    typecheck_payload = row["typecheck"] or {}
    return {
        "id": str(row["id"]),
        "request_text": row["request_text"],
        "inputs": row["inputs"] or {},
        "plan": row["plan"] or {},
        "typecheck": typecheck_payload,
        "approvable": bool(typecheck_payload.get("ok")) and row["status"] == "pending",
        "status": row["status"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "created_at": row["created_at"],
        "run_id": str(row["run_id"]) if row["run_id"] else None,
    }


@router.get("")
async def list_proposals(status: str = "pending", pool=Depends(get_pool)) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, request_text, inputs, plan, typecheck, status, decided_by,
               decided_at, created_at, run_id
        FROM proposals
        WHERE ($1 = 'all' OR status = $1)
        ORDER BY created_at DESC LIMIT 100
        """,
        status,
    )
    return [_row_to_dict(r) for r in rows]


@router.get("/{proposal_id}")
async def get_proposal(proposal_id: UUID, pool=Depends(get_pool)) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT id, request_text, inputs, plan, typecheck, status, decided_by,
               decided_at, created_at, run_id
        FROM proposals WHERE id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no such proposal")
    return _row_to_dict(row)


@router.post("/{proposal_id}")
async def decide(
    proposal_id: UUID, body: Decision, pool=Depends(get_pool)
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT id, request_text, inputs, plan, typecheck, status
        FROM proposals WHERE id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no such proposal")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"proposal was already {row['status']}"
        )

    if body.decision == "reject":
        await pool.execute(
            "UPDATE proposals SET status = 'rejected', decided_by = $2, decided_at = now() "
            "WHERE id = $1",
            proposal_id,
            body.decided_by,
        )
        return {"id": str(proposal_id), "decision": "rejected"}

    stored = row["typecheck"] or {}
    if not stored.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "this proposal failed typecheck and cannot be approved",
                "typecheck": stored,
            },
        )

    services = build_services(pool)
    plan = Plan.model_validate(row["plan"] or {})

    # Re-checked at approval, not trusted from storage. The graph moves
    # between proposal and decision -- a task the plan reuses can be
    # superseded or have its last implementation disabled in the interim, and
    # the stored verdict would still say ok.
    context = await services.graph.load_typecheck_context(plan)
    problems = typecheck(plan, context)
    if problems:
        report = typecheck_report(problems)
        await pool.execute(
            "UPDATE proposals SET typecheck = $2 WHERE id = $1", proposal_id, report
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "the plan no longer typechecks against the current graph",
                "typecheck": report,
            },
        )

    try:
        plan = await services.graph.persist_plan(plan, provenance="company_debate")
    except ValueError as exc:
        # A name collision with a live task carrying a different interface.
        # 409, not 500: the plan is answerable, it just cannot be applied as
        # written against the graph as it stands now.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    run_id = await run_store.create_run(
        pool, row["request_text"], row["inputs"] or {}, plan, status="running"
    )
    await pool.execute(
        """
        UPDATE proposals SET status = 'approved', decided_by = $2, decided_at = now(),
                             run_id = $3, plan = $4
        WHERE id = $1
        """,
        proposal_id,
        body.decided_by,
        run_id,
        plan.model_dump(mode="json"),
    )

    result = await services.executor.execute(plan, row["inputs"] or {}, run_id=run_id)
    await run_store.finish_run(pool, run_id, result)

    return {
        "id": str(proposal_id),
        "decision": "approved",
        "run_id": str(run_id),
        "status": result.status,
        "outputs": result.outputs,
        "error": result.error,
        "stages": [s.model_dump(mode="json") for s in result.stages],
        "total_cost": result.total_cost,
        "total_latency_ms": result.total_latency_ms,
    }
