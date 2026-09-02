"""
Durable, restart-safe execution-graph state (final-V1 hardening §24-§27).

A STATE layer, not a scheduler (§27/§33): no queue, no polling loop. The
existing graph_executor still decides node order; this module records
every node transition to `execution_run_nodes` so a crashed worker can
`resume_run()` and continue.

Transaction shape (important): each node transition is its OWN short
transaction and the `run_node` callback runs OUTSIDE any transaction --
so a slow node can never hold a lock, and a hosted statement timeout
cannot abort a whole run. Single-driver exclusivity comes from a CLAIM on
the run row (status='running' + worker_id + lease_expires_at), not a held
`FOR UPDATE`. A second worker that tries to drive a run under a live lease
gets `ResumeInProgress`.

When a run reaches a terminal state, at most one immutable `executions`
row is appended via the existing `plan_persistence.record_plan_execution`
(only when a `compiled` plan is supplied). `executions` stays append-only
testimony; `execution_runs` is the mutable working state beside it.

  start_run()   -> create execution_runs + execution_run_nodes (pending)
  execute_run() -> claim the run, drive it, persist each transition
  resume_run()  -> reload, skip succeeded, retry/park, continue
  retry_node()  -> explicit bounded retry of one failed/resumable node
  run_status()  -> inspect status + attempts + first-pass vs final
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app.execution.plan_persistence import record_plan_execution

# §33 -- retry is explicit and bounded.
RETRYABLE_ERROR_CLASSES = frozenset({"transient", "timeout", "rate_limit", "network", "conflict"})
NON_RETRYABLE_ERROR_CLASSES = frozenset({"validation", "auth", "not_found", "logic", "cancelled"})

NODE_LEASE_SECONDS = 300
RUN_LEASE_SECONDS = 600

RunNodeCB = Callable[[int, int], Awaitable[dict[str, Any]]]  # (node_order, attempt) -> result_ref; raises on failure


class DurableRunError(RuntimeError):
    pass


class ResumeInProgress(DurableRunError):
    """Another worker holds this run under a live lease -- back off, don't retry."""


class WorkerLost(BaseException):
    """Raised by a run_node callback to simulate the worker dying MID-NODE.
    The node is left 'running' with an already-expired lease -- exactly
    what a real crash leaves -- and the exception propagates out rather
    than being recorded as a node failure. A later resume_run() detects
    the stale 'running' lease and re-arms (pure) or parks (side-effecting)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if any(t in name or t in msg for t in ("timeout", "timederror")):
        return "timeout"
    if any(t in name or t in msg for t in ("connection", "network", "socket", "dns", "unreachable")):
        return "network"
    if "rate" in msg and "limit" in msg:
        return "rate_limit"
    if any(t in name for t in ("transient", "temporar")):
        return "transient"
    if any(t in name or t in msg for t in ("permission", "unauthor", "forbidden", "auth")):
        return "auth"
    if any(t in name or t in msg for t in ("notfound", "not_found", "missing", "does not exist")):
        return "not_found"
    if any(t in name for t in ("value", "type", "assert", "validation", "schema")):
        return "validation"
    if isinstance(exc, asyncpg.exceptions.SerializationError):
        return "conflict"
    return "logic"


def _is_retryable(error_class: Optional[str]) -> bool:
    return error_class in RETRYABLE_ERROR_CLASSES


# ---------------------------------------------------------------------------
async def start_run(
    pool: asyncpg.Pool, *, execution_plan_id: str, task_graph_id: str,
    procedure_id: str, procedure_version: int, node_orders: list[int],
    deps: dict[int, list[int]], side_effecting: Optional[set[int]] = None,
    max_attempts: int = 3, parameters: Optional[dict] = None,
    created_by: Optional[str] = None, scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> str:
    side_effecting = side_effecting or set()
    async with pool.acquire() as conn, conn.transaction():
        run_id = await conn.fetchval(
            "INSERT INTO execution_runs (execution_plan_id, task_graph_id, procedure_id, "
            " procedure_version, status, parameters, created_by, scope_type, scope_entity_id, "
            " started_at) VALUES ($1,$2,$3,$4,'pending',$5::jsonb,$6,$7,$8, now()) RETURNING id",
            execution_plan_id, task_graph_id, procedure_id, procedure_version,
            json.dumps(parameters or {}), created_by, scope_type, scope_entity_id,
        )
        for order in node_orders:
            await conn.execute(
                "INSERT INTO execution_run_nodes (execution_run_id, node_order, status, "
                " max_attempts, side_effecting) VALUES ($1,$2,'pending',$3,$4)",
                run_id, order, max_attempts, order in side_effecting,
            )
    return str(run_id)


async def _claim_run(pool: asyncpg.Pool, run_id: str, worker_id: str) -> dict:
    """Take the run's single-driver lease. Raises ResumeInProgress if a
    different worker holds it under a lease that has not expired."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "UPDATE execution_runs SET status = CASE WHEN status IN ('pending','paused','failed') "
            "   THEN 'running' ELSE status END, "
            " worker_id=$2, lease_expires_at = now() + make_interval(secs => $3) "
            "WHERE id=$1 AND status NOT IN ('succeeded','cancelled') "
            "  AND (worker_id IS NULL OR worker_id=$2 OR lease_expires_at IS NULL OR lease_expires_at < now()) "
            "RETURNING *",
            run_id, worker_id, RUN_LEASE_SECONDS,
        )
        if row is not None:
            return dict(row)
        cur = await conn.fetchrow("SELECT id, status, worker_id FROM execution_runs WHERE id=$1", run_id)
    if cur is None:
        raise DurableRunError(f"execution_run {run_id} not found")
    if cur["status"] in ("succeeded", "cancelled"):
        return {"id": str(cur["id"]), "status": cur["status"], "_terminal": True}
    raise ResumeInProgress(f"execution_run {run_id} held by {cur['worker_id']!r} under a live lease")


async def _release_run(pool: asyncpg.Pool, run_id: str, worker_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE execution_runs SET worker_id=NULL, lease_expires_at=NULL "
            "WHERE id=$1 AND worker_id=$2 AND status NOT IN ('succeeded','failed')",
            run_id, worker_id,
        )


async def _load(pool: asyncpg.Pool, run_id: str) -> tuple[dict, dict[int, dict]]:
    async with pool.acquire() as conn:
        run = dict(await conn.fetchrow("SELECT * FROM execution_runs WHERE id=$1", run_id))
        nodes = {
            r["node_order"]: dict(r) for r in await conn.fetch(
                "SELECT * FROM execution_run_nodes WHERE execution_run_id=$1 ORDER BY node_order", run_id,
            )
        }
    return run, nodes


def _ready(order: int, deps: dict[int, list[int]], nodes: dict[int, dict]) -> bool:
    return all(nodes[d]["status"] == "succeeded" for d in deps.get(order, []))


def _blocked(order: int, deps: dict[int, list[int]], nodes: dict[int, dict]) -> bool:
    return any(nodes[d]["status"] in ("failed", "blocked", "cancelled") for d in deps.get(order, []))


async def _node_claim(pool: asyncpg.Pool, node: dict, attempt: int, worker_id: str) -> bool:
    async with pool.acquire() as conn:
        got = await conn.fetchval(
            "UPDATE execution_run_nodes SET status='running', attempt_count=$2, worker_id=$3, "
            " lease_expires_at = now() + make_interval(secs => $4), "
            " started_at = COALESCE(started_at, now()), error_ref='{}'::jsonb "
            "WHERE id=$1 AND status IN ('pending','failed','resumable') "
            "  AND (worker_id IS NULL OR worker_id=$3 OR lease_expires_at IS NULL OR lease_expires_at < now()) "
            "RETURNING id",
            node["id"], attempt, worker_id, NODE_LEASE_SECONDS,
        )
    return got is not None


async def _node_finish(pool: asyncpg.Pool, node_id: str, worker_id: str, *,
                       ok: bool, result: Optional[dict] = None,
                       error_class: Optional[str] = None, error: Optional[dict] = None) -> None:
    async with pool.acquire() as conn:
        if ok:
            await conn.execute(
                "UPDATE execution_run_nodes SET status='succeeded', ended_at=now(), "
                " result_ref=$2::jsonb, verification_state='verified', worker_id=NULL, lease_expires_at=NULL "
                "WHERE id=$1 AND worker_id=$3",
                node_id, json.dumps(result or {}), worker_id,
            )
        else:
            await conn.execute(
                "UPDATE execution_run_nodes SET status='failed', ended_at=now(), "
                " error_class=$2, error_ref=$3::jsonb, verification_state='failed', "
                " worker_id=NULL, lease_expires_at=NULL "
                "WHERE id=$1 AND worker_id=$4",
                node_id, error_class, json.dumps(error or {}), worker_id,
            )


async def _mark(pool: asyncpg.Pool, node_id: str, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE execution_run_nodes SET status=$2, worker_id=NULL WHERE id=$1",
                           node_id, status)


async def _run_one_node(pool: asyncpg.Pool, order: int, node: dict, *,
                        run_node: RunNodeCB, worker_id: str) -> str:
    """One node: claim -> callback (NO transaction held) -> record. Retries
    inline while the failure class is retryable and attempts remain."""
    max_attempts = node["max_attempts"]
    attempt = node["attempt_count"]
    while attempt < max_attempts:
        attempt += 1
        if not await _node_claim(pool, node, attempt, worker_id):
            _, fresh = await _load(pool, node["execution_run_id"])
            return fresh[order]["status"]
        try:
            result = await run_node(order, attempt)
        except WorkerLost:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE execution_run_nodes SET lease_expires_at = now() - interval '1 second' WHERE id=$1",
                    node["id"])
            raise
        except BaseException as exc:  # noqa: BLE001 -- a node failure is data
            ec = classify_error(exc)
            await _node_finish(pool, node["id"], worker_id, ok=False, error_class=ec,
                               error={"type": type(exc).__name__, "message": str(exc)[:500]})
            if _is_retryable(ec) and attempt < max_attempts:
                _, fresh = await _load(pool, node["execution_run_id"])
                node = fresh[order]
                continue
            return "failed"
        else:
            await _node_finish(pool, node["id"], worker_id, ok=True, result=result or {})
            return "succeeded"
    return "failed"


async def _drive(pool: asyncpg.Pool, run_id: str, *, deps: dict[int, list[int]],
                 run_node: RunNodeCB, worker_id: str, compiled=None) -> dict[str, Any]:
    progressed = True
    while progressed:
        progressed = False
        run, nodes = await _load(pool, run_id)
        if run["status"] in ("succeeded", "failed", "cancelled"):
            return await _finalize(pool, run_id, compiled=compiled)
        for order, node in nodes.items():
            if node["status"] in ("succeeded", "cancelled"):
                continue
            if node["status"] == "running" and node["lease_expires_at"] and node["lease_expires_at"] < _now():
                # crashed mid-node. §32: park a side-effecting node, retry a pure one.
                if node["side_effecting"]:
                    await _mark(pool, node["id"], "resumable")
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE execution_runs SET status='paused' WHERE id=$1 AND status='running'", run_id)
                    return {"run_id": run_id, "status": "paused",
                            "note": f"node {order} (side-effecting) crashed mid-flight -- parked; "
                                    "call retry_node() with an explicit decision",
                            "nodes": _node_summary((await _load(pool, run_id))[1])}
                await _mark(pool, node["id"], "resumable")
                _, nodes = await _load(pool, run_id)
                node = nodes[order]
            if node["status"] == "failed":
                continue  # failed nodes only re-run via resume_run pre-pass / retry_node
            if _blocked(order, deps, nodes):
                if node["status"] != "blocked":
                    await _mark(pool, node["id"], "blocked")
                    progressed = True
                continue
            if node["status"] == "blocked":
                # a blocker recovered (retry_node / resume) -- re-arm this node
                await _mark(pool, node["id"], "pending")
                _, nodes = await _load(pool, run_id)
                node = nodes[order]
                progressed = True
            if not _ready(order, deps, nodes):
                continue
            outcome = await _run_one_node(pool, order, node, run_node=run_node, worker_id=worker_id)
            progressed = True
            if outcome != "succeeded":
                break
    return await _finalize(pool, run_id, compiled=compiled)


async def _finalize(pool: asyncpg.Pool, run_id: str, *, compiled=None) -> dict[str, Any]:
    run, nodes = await _load(pool, run_id)
    statuses = {n["status"] for n in nodes.values()}
    if statuses and statuses <= {"succeeded"}:
        run_status_v, outcome = "succeeded", "success"
    elif statuses & {"failed", "blocked"}:
        run_status_v = "failed"
        outcome = "needs_rework" if "succeeded" in statuses else "failure"
    else:
        return {"run_id": run_id, "status": run["status"], "nodes": _node_summary(nodes),
                "note": "run not terminal -- resumable"}

    exec_id = None
    async with pool.acquire() as conn, conn.transaction():
        if compiled is not None:
            exec_id = str(await record_plan_execution(
                conn, compiled=compiled, outcome=outcome, created_by=run.get("created_by"),
                scope_type=run.get("scope_type"), scope_entity_id=run.get("scope_entity_id"),
            ))
        await conn.execute(
            "UPDATE execution_runs SET status=$2, final_outcome=$3, final_execution_id=$4, "
            " ended_at=now(), worker_id=NULL, lease_expires_at=NULL "
            "WHERE id=$1 AND status NOT IN ('succeeded','failed','cancelled')",
            run_id, run_status_v, outcome, exec_id,
        )
    run, nodes = await _load(pool, run_id)
    return {"run_id": run_id, "status": run["status"], "final_outcome": run["final_outcome"],
            "final_execution_id": str(run["final_execution_id"]) if run["final_execution_id"] else None,
            "resume_count": run["resume_count"], "nodes": _node_summary(nodes)}


def _node_summary(nodes: dict[int, dict]) -> list[dict[str, Any]]:
    return [
        {"node_order": o, "status": n["status"], "attempt_count": n["attempt_count"],
         "max_attempts": n["max_attempts"], "error_class": n["error_class"],
         "side_effecting": n["side_effecting"], "verification_state": n["verification_state"],
         "first_pass_success": (n["status"] == "succeeded" and n["attempt_count"] <= 1)}
        for o, n in sorted(nodes.items())
    ]


# ---------------------------------------------------------------------------
async def execute_run(
    pool: asyncpg.Pool, run_id: str, *, deps: dict[int, list[int]],
    run_node: RunNodeCB, worker_id: str = "worker-1", compiled=None,
) -> dict[str, Any]:
    run = await _claim_run(pool, run_id, worker_id)
    if run.get("_terminal"):
        _, nodes = await _load(pool, run_id)
        return {"run_id": run_id, "status": run["status"], "note": "already terminal",
                "nodes": _node_summary(nodes)}
    try:
        return await _drive(pool, run_id, deps=deps, run_node=run_node,
                            worker_id=worker_id, compiled=compiled)
    finally:
        # Always free the run's driver lease -- on a normal return AND on a
        # WorkerLost crash (nobody is driving after a crash; the node's own
        # 'running' + expired lease is what signals "resume needed").
        await _release_run(pool, run_id, worker_id)


async def resume_run(
    pool: asyncpg.Pool, run_id: str, *, deps: dict[int, list[int]],
    run_node: RunNodeCB, worker_id: str = "worker-2", compiled=None,
) -> dict[str, Any]:
    """
    Reload persisted state and continue. §31: succeeded nodes are NOT
    re-run. A durably-failed node whose class is retryable with attempts
    left is re-armed; a non-retryable or exhausted one stays failed. A
    node stuck 'running' with an expired lease is a mid-node crash ->
    re-armed if pure, parked if side-effecting (§32). An already-terminal
    run is an idempotent no-op.
    """
    run = await _claim_run(pool, run_id, worker_id)
    if run.get("_terminal") or run["status"] in ("succeeded", "failed", "cancelled"):
        r2, nodes = await _load(pool, run_id)
        return {"run_id": run_id, "status": r2["status"], "note": "already terminal -- resume is a no-op",
                "resume_count": r2["resume_count"], "nodes": _node_summary(nodes)}
    async with pool.acquire() as conn:
        await conn.execute("UPDATE execution_runs SET resume_count = resume_count + 1 WHERE id=$1", run_id)
    _, nodes = await _load(pool, run_id)
    for n in nodes.values():
        if n["status"] == "failed" and _is_retryable(n["error_class"]) and n["attempt_count"] < n["max_attempts"]:
            await _mark(pool, n["id"], "resumable")
    try:
        return await _drive(pool, run_id, deps=deps, run_node=run_node,
                            worker_id=worker_id, compiled=compiled)
    finally:
        # Always free the run's driver lease -- on a normal return AND on a
        # WorkerLost crash (nobody is driving after a crash; the node's own
        # 'running' + expired lease is what signals "resume needed").
        await _release_run(pool, run_id, worker_id)


async def retry_node(
    pool: asyncpg.Pool, run_id: str, node_order: int, *, deps: dict[int, list[int]],
    run_node: RunNodeCB, worker_id: str = "worker-retry", compiled=None, force: bool = False,
) -> dict[str, Any]:
    """
    Explicit bounded retry of ONE failed / resumable / blocked node (§34).
    `force=True` bumps that node's max_attempts by 1 (operator override
    for an exhausted node). A succeeded node is never retried.
    """
    run = await _claim_run(pool, run_id, worker_id)
    if run.get("_terminal"):
        return {"run_id": run_id, "status": run["status"], "note": "run already terminal"}
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchrow(
                "SELECT * FROM execution_run_nodes WHERE execution_run_id=$1 AND node_order=$2",
                run_id, node_order)
            if n is None:
                raise DurableRunError(f"node {node_order} not in run {run_id}")
            if n["status"] == "succeeded":
                return {"run_id": run_id, "node_order": node_order, "status": "succeeded",
                        "note": "already succeeded -- terminal, not retried"}
            if not force and n["status"] not in ("failed", "resumable", "blocked"):
                return {"run_id": run_id, "node_order": node_order, "status": n["status"],
                        "note": "node is not in a retryable state"}
            if force:
                await conn.execute(
                    "UPDATE execution_run_nodes SET max_attempts = max_attempts + 1, status='resumable', "
                    " worker_id=NULL WHERE id=$1", n["id"])
            else:
                await conn.execute(
                    "UPDATE execution_run_nodes SET status='resumable', worker_id=NULL WHERE id=$1", n["id"])
            await conn.execute(
                "UPDATE execution_runs SET status='running' WHERE id=$1 AND status IN ('paused','failed','pending')",
                run_id)
        return await _drive(pool, run_id, deps=deps, run_node=run_node,
                            worker_id=worker_id, compiled=compiled)
    finally:
        await _release_run(pool, run_id, worker_id)


async def run_status(pool: asyncpg.Pool, run_id: str) -> dict[str, Any]:
    run, nodes = await _load(pool, run_id)
    summ = _node_summary(nodes)
    return {
        "run_id": run_id, "status": run["status"], "final_outcome": run["final_outcome"],
        "final_execution_id": str(run["final_execution_id"]) if run["final_execution_id"] else None,
        "resume_count": run["resume_count"], "started_at": run["started_at"], "ended_at": run["ended_at"],
        "nodes": summ,
        "first_pass_success": all(s["first_pass_success"] for s in summ) if run["status"] == "succeeded" else None,
    }
