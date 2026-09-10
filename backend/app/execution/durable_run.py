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

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app.execution import recorder as _rec
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
    # MCP hardening B3: ProcedureRun identity fields (migration 51).
    # All optional/None for every existing caller -- byte-identical
    # behavior when omitted.
    request_id: Optional[str] = None, workspace_id: Optional[str] = None,
    trace_id: Optional[str] = None, parent_run_id: Optional[str] = None,
    parent_node_id: Optional[str] = None,
    claim_working_set_revision: Optional[datetime] = None,
    route_decision_id: Optional[str] = None,
    verification_plan_id: Optional[str] = None,
) -> str:
    """
    B4's "create or return a durable ProcedureRun": when `request_id` is
    given and a run already exists with that exact request_id, THAT run's
    id is returned unchanged -- no new row, no duplicate execution_run_nodes
    -- rather than creating a second run for what is semantically the same
    accepted-procedure decision replayed (an idempotency key, not a lookup
    convenience). Without `request_id` (every pre-existing caller), this is
    byte-identical to before: always insert a fresh run.
    """
    side_effecting = side_effecting or set()
    if request_id is not None:
        existing = await pool.fetchval(
            "SELECT id FROM execution_runs WHERE request_id = $1", request_id,
        )
        if existing is not None:
            return str(existing)

    # MCP hardening B9-B13 (migration 52): root_run_id is a materialized-
    # path denormalization of the SAME parent_run_id chain -- a root run
    # (no parent) points at itself; a child copies its parent's
    # root_run_id. Read the parent's root BEFORE inserting so the new row
    # never has a null-then-backfilled root_run_id window.
    root_run_id: Optional[str] = None
    if parent_run_id is not None:
        parent_row = await pool.fetchrow(
            "SELECT root_run_id, trace_id FROM execution_runs WHERE id = $1", parent_run_id,
        )
        if parent_row is None or parent_row["root_run_id"] is None:
            raise DurableRunError(
                f"parent_run_id {parent_run_id} not found or has no root_run_id"
            )
        root_run_id = str(parent_row["root_run_id"])
        # B16: a child ALWAYS inherits its parent's real trace_id -- one
        # causal chain, regardless of what (if anything) this specific
        # call passed. Overrides an explicitly-passed trace_id too: a
        # child cannot legitimately start a NEW trace, matching B16's own
        # framing ("trace reference" ties a whole execution together).
        if parent_row["trace_id"] is not None:
            trace_id = str(parent_row["trace_id"])

    # B16: "terminal state + outcome + verification + evidence + trace
    # reference" -- every run gets a REAL trace_id, generated HERE (the
    # one real canonical entry point every caller goes through), not
    # only by callers that happen to resolve one themselves first
    # (server.py's `_resolve_trace_id` still does that for find_best_way
    # specifically, to inherit across a parent/child pair it already
    # knows about before this call -- this is the backstop for every
    # other real or future caller, never leaving trace_id NULL).
    if trace_id is None:
        from app.utils.ids import uuid7
        trace_id = str(uuid7())

    try:
        async with pool.acquire() as conn, conn.transaction():
            run_id = await conn.fetchval(
                "INSERT INTO execution_runs (execution_plan_id, task_graph_id, procedure_id, "
                " procedure_version, status, parameters, created_by, scope_type, scope_entity_id, "
                " request_id, workspace_id, trace_id, parent_run_id, parent_node_id, "
                " claim_working_set_revision, route_decision_id, root_run_id, verification_plan_id, started_at) "
                "VALUES ($1,$2,$3,$4,'pending',$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17, now()) "
                "RETURNING id",
                execution_plan_id, task_graph_id, procedure_id, procedure_version,
                # Raw Python dict, NOT json.dumps()'d -- the pool's
                # registered jsonb codec (app/db/session.py::
                # _init_connection) already encodes this; pre-encoding
                # here double-encodes (confirmed empirically: a
                # pre-dumped string bound to a jsonb column round-trips
                # as a JSON STRING containing the object's text, not the
                # real object -- fixed here and at the two other
                # `json.dumps()` call sites below in this same function).
                parameters or {}, created_by, scope_type, scope_entity_id,
                request_id, workspace_id, trace_id, parent_run_id, parent_node_id,
                claim_working_set_revision, route_decision_id, root_run_id, verification_plan_id,
            )
            if root_run_id is None:
                # A fresh root: points at itself. Done in the same
                # transaction as the INSERT above so no reader can ever
                # observe a row with root_run_id still NULL.
                await conn.execute(
                    "UPDATE execution_runs SET root_run_id = id WHERE id = $1", run_id,
                )
            for order in node_orders:
                await conn.execute(
                    "INSERT INTO execution_run_nodes (execution_run_id, node_order, status, "
                    " max_attempts, side_effecting) VALUES ($1,$2,'pending',$3,$4)",
                    run_id, order, max_attempts, order in side_effecting,
                )
            # B7/B8: durable event log, same transaction as the row it
            # describes -- never observable as "created but not recorded".
            await _rec.record_run_created(
                conn, str(run_id), procedure_id=str(procedure_id), procedure_version=procedure_version,
                parent_run_id=str(parent_run_id) if parent_run_id else None,
                root_run_id=str(root_run_id) if root_run_id else str(run_id),
            )
            if route_decision_id is not None:
                await _rec.record_route_decided(conn, str(run_id), route_decision_id=route_decision_id, route=None)
            if parent_run_id is not None:
                parent_node_order = None
                if parent_node_id is not None:
                    parent_node_order = await conn.fetchval(
                        "SELECT node_order FROM execution_run_nodes WHERE id = $1", parent_node_id,
                    )
                await _rec.record_child_run(
                    conn, str(parent_run_id), parent_node_order=parent_node_order,
                    child_run_id=str(run_id), child_procedure_id=str(procedure_id),
                    child_procedure_version=procedure_version,
                )
    except asyncpg.UniqueViolationError:
        # Concurrent create_or_return race on the same request_id: the
        # other insert won, this one lost the unique index -- return the
        # winner's row rather than raising, which is what "idempotent"
        # actually has to mean under real concurrency (CLAUDE.md rule 12).
        if request_id is None:
            raise
        winner = await pool.fetchval(
            "SELECT id FROM execution_runs WHERE request_id = $1", request_id,
        )
        if winner is None:
            raise
        return str(winner)
    return str(run_id)


async def _claim_run(pool: asyncpg.Pool, run_id: str, worker_id: str) -> dict:
    """Take the run's single-driver lease. Raises ResumeInProgress if a
    different worker holds it under a lease that has not expired."""
    async with pool.acquire() as conn, conn.transaction():
        prior_status = await conn.fetchval("SELECT status FROM execution_runs WHERE id=$1 FOR UPDATE", run_id)
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
            if prior_status in ("pending", "paused", "failed"):
                await _rec.record_run_claimed(conn, run_id, worker_id=worker_id, from_status=prior_status)
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
    async with pool.acquire() as conn, conn.transaction():
        got = await conn.fetchval(
            "UPDATE execution_run_nodes SET status='running', attempt_count=$2, worker_id=$3, "
            " lease_expires_at = now() + make_interval(secs => $4), "
            " started_at = COALESCE(started_at, now()), error_ref='{}'::jsonb "
            "WHERE id=$1 AND status IN ('pending','failed','resumable') "
            "  AND (worker_id IS NULL OR worker_id=$3 OR lease_expires_at IS NULL OR lease_expires_at < now()) "
            "RETURNING id",
            node["id"], attempt, worker_id, NODE_LEASE_SECONDS,
        )
        if got is not None:
            await _rec.record_node_claimed(
                conn, node["execution_run_id"], node_order=node["node_order"],
                worker_id=worker_id, attempt=attempt,
            )
    return got is not None


async def _node_finish(pool: asyncpg.Pool, node_id: str, worker_id: str, *,
                       ok: bool, result: Optional[dict] = None,
                       error_class: Optional[str] = None, error: Optional[dict] = None,
                       execution_run_id: Optional[str] = None, node_order: Optional[int] = None) -> None:
    async with pool.acquire() as conn, conn.transaction():
        if ok:
            tag = await conn.execute(
                "UPDATE execution_run_nodes SET status='succeeded', ended_at=now(), "
                " result_ref=$2::jsonb, verification_state='verified', worker_id=NULL, lease_expires_at=NULL "
                "WHERE id=$1 AND worker_id=$3",
                node_id, result or {}, worker_id,
            )
            if tag != "UPDATE 0" and execution_run_id is not None:
                await _rec.record_node_succeeded(conn, execution_run_id, node_order=node_order)
                # B7's record_artifact(): a real Adapter.execute() (B25)
                # composition populates NodeResult.data["artifacts"] with
                # real references (never inline content). Checked at both
                # the top level (an ad-hoc run_node closure that returns
                # artifacts directly) and under "data" (durable_resume.py
                # ::_make_runner's real NodeResult -> dict wrapping,
                # {"notes":..., "data": dict(result.data), "attempt":...})
                # -- one durable event per artifact, same transaction as
                # the node succeeding.
                result = result or {}
                artifacts_seen = result.get("artifacts") or (result.get("data") or {}).get("artifacts") or []
                for artifact in artifacts_seen:
                    if not isinstance(artifact, dict) or not artifact.get("ref"):
                        continue
                    await _rec.record_artifact(
                        conn, execution_run_id, node_order=node_order,
                        kind=artifact.get("kind", "unknown"), ref=artifact["ref"],
                        sha256=artifact.get("sha256"), size_bytes=artifact.get("size_bytes"),
                    )
        else:
            tag = await conn.execute(
                "UPDATE execution_run_nodes SET status='failed', ended_at=now(), "
                " error_class=$2, error_ref=$3::jsonb, verification_state='failed', "
                " worker_id=NULL, lease_expires_at=NULL "
                "WHERE id=$1 AND worker_id=$4",
                node_id, error_class, error or {}, worker_id,
            )
            if tag != "UPDATE 0" and execution_run_id is not None:
                await _rec.record_node_failed(conn, execution_run_id, node_order=node_order, error_class=error_class)


async def report_node_progress(
    pool: asyncpg.Pool, execution_run_id: str, node_order: int, *, ok: bool,
    result: Optional[dict] = None, error_class: Optional[str] = None,
    error: Optional[dict] = None, worker_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    MCP hardening B6: host-executed Procedure lease progress reporting.
    A caller who executed ONE node OUTSIDE Stealth's own sandbox (the
    `plan_only`/`continue_run` pattern -- the node was never driven by
    `execute_run`/`resume_run`'s own `run_node` callback) reports that
    node's REAL outcome here, and it transitions through the EXACT SAME
    `_node_claim`/`_node_finish` mechanics the server's own driving loop
    uses -- no second state-transition path, and migration 36's
    terminal-state fence trigger still applies exactly as it does for a
    server-driven node (an already-`succeeded` node cannot be rewritten
    by this function either).

    Fails closed on a genuine claim conflict (the node is not in
    `pending`/`failed`/`resumable`, or is already claimed by a live
    different worker) -- returns `{"claimed": False, "status": ...}`
    rather than silently overwriting another claim. Never invents a
    result for a node it could not actually claim.
    """
    _, nodes = await _load(pool, execution_run_id)
    if node_order not in nodes:
        raise DurableRunError(f"no node at order {node_order} for execution_run_id {execution_run_id}")
    node = nodes[node_order]
    worker_id = worker_id or f"host-report-{node['id']}"
    attempt = node["attempt_count"] + 1
    claimed = await _node_claim(pool, node, attempt, worker_id)
    if not claimed:
        _, fresh = await _load(pool, execution_run_id)
        return {"claimed": False, "status": fresh[node_order]["status"]}
    await _node_finish(
        pool, node["id"], worker_id, ok=ok, result=result, error_class=error_class, error=error,
        execution_run_id=execution_run_id, node_order=node_order,
    )
    _, fresh = await _load(pool, execution_run_id)
    return {"claimed": True, "status": fresh[node_order]["status"]}


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
                               error={"type": type(exc).__name__, "message": str(exc)[:500]},
                               execution_run_id=node["execution_run_id"], node_order=order)
            if _is_retryable(ec) and attempt < max_attempts:
                _, fresh = await _load(pool, node["execution_run_id"])
                node = fresh[order]
                continue
            return "failed"
        else:
            await _node_finish(pool, node["id"], worker_id, ok=True, result=result or {},
                               execution_run_id=node["execution_run_id"], node_order=order)
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
                    async with pool.acquire() as conn, conn.transaction():
                        tag = await conn.execute(
                            "UPDATE execution_runs SET status='paused' WHERE id=$1 AND status='running'", run_id)
                        if tag != "UPDATE 0":
                            await _rec.record_run_paused(
                                conn, run_id, node_order=order,
                                reason=f"node {order} (side-effecting) crashed mid-flight",
                            )
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
            impl_id = None
            try:
                from app.execution.implementation_executor import plan_implementation_id
                impl_id = plan_implementation_id(compiled)
            except Exception:  # noqa: BLE001 -- an unbound plan is fine, record None
                impl_id = None
            exec_id = str(await record_plan_execution(
                conn, compiled=compiled, outcome=outcome, created_by=run.get("created_by"),
                scope_type=run.get("scope_type"), scope_entity_id=run.get("scope_entity_id"),
                implementation_id=impl_id,
            ))
        tag = await conn.execute(
            "UPDATE execution_runs SET status=$2, final_outcome=$3, final_execution_id=$4, "
            " ended_at=now(), worker_id=NULL, lease_expires_at=NULL "
            "WHERE id=$1 AND status NOT IN ('succeeded','failed','cancelled')",
            run_id, run_status_v, outcome, exec_id,
        )
        if tag != "UPDATE 0":
            await _rec.record_run_finalized(conn, run_id, status=run_status_v, outcome=outcome)
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


async def record_run_usage(
    pool: asyncpg.Pool, run_id: str, *, tokens: int = 0, tool_calls: int = 0, cost_usd: float = 0.0,
) -> None:
    """MCP hardening B12: real, ATOMIC accumulation of this run's own
    token/tool-call/cost usage (migration 74) -- called by a real caller
    after it has genuinely spent them (server.py's tier-2 path, which
    already aggregates real `AgentRun.usage`/`tool_calls` counts), never
    a guessed or hardcoded figure. `recursion_guard.check_recursion_
    limits` sums this across the whole ancestor chain to enforce the
    configured budgets."""
    if tokens == 0 and tool_calls == 0 and cost_usd == 0.0:
        return
    await pool.execute(
        "UPDATE execution_runs SET tokens_used = tokens_used + $2, "
        " tool_calls_used = tool_calls_used + $3, cost_usd_used = cost_usd_used + $4 "
        "WHERE id = $1",
        run_id, tokens, tool_calls, cost_usd,
    )


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
