"""
Context-free entrypoints over the proven durable-run service
(`app/execution/durable_run.py`), so a REST or MCP caller -- which cannot
pass a Python `run_node` callback or a `deps` map -- can still inspect,
resume, and retry a durable execution run (final-V1 §2, §34).

WHAT THIS ADDS, and nothing more:

  - `run_status_by_id` / `node_history_by_id` -- pure reads, thin
    pass-throughs to `durable_run.run_status` and `execution_run_nodes`.
  - `resume_run_by_id` / `retry_run_node_by_id` -- rebuild the
    `CompiledPlan` + `deps` from the persisted `execution_plans` /
    `task_graphs` rows (via `plan_persistence._row_to_compiled_plan`),
    build a context-free `run_node` that dispatches each node through the
    REAL `implementation_executor.execute_implementation` (resolve the
    node's pinned implementation -> `providers.get_provider(kind).execute`),
    and hand that to `durable_run.resume_run` / `retry_node`.

  NO retry / resume / scheduling logic lives here: every state
  transition, lease, terminal fence and attempt bound is still owned by
  `durable_run`. This module only removes the "you need a Python
  callback" barrier.

WHAT IT REFUSES TO DO (honest, never a fabricated success):

  A run whose plan was compiled by the coding-agent plan compilers
  (`execution_plans.extractor_version` like
  `find_best_way_plan_compiler@1` / `reproduce_procedure_plan_compiler@1`),
  or whose still-pending nodes carry no durable implementation binding /
  a `kind` with no real provider (`providers.PROVIDER_REGISTRY` today
  only realises `frontier` and `deterministic`), needs the live
  coding-agent sandbox to execute. This module does NOT fake that. It
  returns a structured `{"status": "needs_product_context", ...}` that
  points the caller at the product tool
  (`find_best_way(resume_run_id=...)` / `reproduce_procedure`), which
  owns the sandbox context.

AUTHORIZATION (final-V1 §2, §9): the two MUTATING entrypoints take an
`actor_id` -- the resolved caller identity. `execution_runs` has no
owner/visibility column; the gate is `actor_id == execution_runs.created_by`
when BOTH are known, else allow (matching every other write path's
"no identity resolved -> fall back" posture). A caller whose identity IS
resolvable and differs from `created_by` gets `NotYourRun`. Reads are
open (same posture as every other read surface).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app.execution import durable_run as _dr
from app.execution import providers
from app.execution.implementation_executor import execute_implementation
from app.execution.plan_persistence import _row_to_compiled_plan
from app.services.access import AccessScope

# `execution_plans.extractor_version` prefixes whose compiled nodes are
# coding-agent / sandbox steps -- they cannot be executed context-free.
CODING_AGENT_EXTRACTOR_PREFIXES = (
    "find_best_way_plan_compiler",
    "reproduce_procedure_plan_compiler",
)

NEEDS_PRODUCT_CONTEXT_DETAIL = (
    "this run's nodes require the coding-agent sandbox; resume it by "
    "calling MCP find_best_way(resume_run_id=...) / reproduce_procedure "
    "with the original repo_path"
)


class NotYourRun(_dr.DurableRunError):
    """The resolved caller identity differs from `execution_runs.created_by`
    -- a caller may not resume/retry another user's run."""


def _is_coding_agent_plan(extractor_version: Optional[str]) -> bool:
    ev = extractor_version or ""
    return any(ev == p or ev.startswith(p + "@") or ev.startswith(p)
               for p in CODING_AGENT_EXTRACTOR_PREFIXES)


def _needs_product_context(run_id: str) -> dict[str, Any]:
    return {
        "status": "needs_product_context",
        "detail": NEEDS_PRODUCT_CONTEXT_DETAIL,
        "run_id": str(run_id),
    }


def resolved_caller_identity_or_none() -> Optional[str]:
    """The real, resolved caller identity, or None when none resolves --
    the two REAL identity sources `server.py::_resolve_caller_identity`
    checks, but returning None instead of a tool-name fallback so an
    authorization gate can tell "identity known" from "identity unknown".
    """
    try:  # 1. the MCP SDK's own per-request access-token contextvar
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is not None and getattr(token, "subject", None):
            return str(token.subject)
    except Exception:  # noqa: BLE001 -- absent in a non-MCP process, fine
        pass
    try:  # 2. the OIDC actor contextvar authn's ASGI middleware populates
        from app.services.authn import current_actor_id

        actor_id = current_actor_id()
        if actor_id:
            return str(actor_id)
    except Exception:  # noqa: BLE001
        pass
    return None


def authorize_run_mutation(run_created_by: Optional[str], actor_id: Optional[str]) -> None:
    """Raise `NotYourRun` iff BOTH identities are known and they differ.
    Unknown on either side -> allowed (the fresh-start / header-identity
    posture every other write path in this repo uses)."""
    if actor_id and run_created_by and str(actor_id) != str(run_created_by):
        raise NotYourRun(
            f"run created_by {run_created_by!r} -- caller {actor_id!r} may not mutate it"
        )


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
async def _run_row(pool: asyncpg.Pool, run_id: str) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM execution_runs WHERE id = $1", run_id)


async def run_status_by_id(pool: asyncpg.Pool, run_id: str) -> Optional[dict[str, Any]]:
    """Thin pass-through to `durable_run.run_status` (pure read). `None`
    when the run does not exist -- callers 404/REFUSE on that."""
    if await _run_row(pool, run_id) is None:
        return None
    status = await _dr.run_status(pool, run_id)
    row = await _run_row(pool, run_id)
    status["created_by"] = row["created_by"]
    status["scope_type"] = row["scope_type"]
    status["scope_entity_id"] = row["scope_entity_id"]
    status["worker_id"] = row["worker_id"]
    status["lease_expires_at"] = row["lease_expires_at"]
    return status


async def node_history_by_id(pool: asyncpg.Pool, run_id: str) -> Optional[dict[str, Any]]:
    """Per-node attempt history: the full `execution_run_nodes` rows
    (status, attempt_count/max_attempts, error_class + error_ref, the
    pinned implementation binding, worker/lease, first-pass vs final).
    `None` when the run does not exist."""
    row = await _run_row(pool, run_id)
    if row is None:
        return None
    async with pool.acquire() as conn:
        nodes = await conn.fetch(
            "SELECT node_order, status, attempt_count, max_attempts, "
            " implementation_id, implementation_version, side_effecting, "
            " error_class, error_ref, result_ref, verification_state, "
            " worker_id, lease_expires_at, started_at, ended_at "
            "FROM execution_run_nodes WHERE execution_run_id = $1 ORDER BY node_order",
            run_id,
        )
        # Best-effort: the durable implementation each node's PLAN binds
        # (execution_run_nodes only carries a binding once durable_run pins
        # one; the compiled plan node is the other place it lives).
        graph_row = await conn.fetchrow(
            "SELECT tg.nodes FROM task_graphs tg "
            "JOIN execution_plans ep ON ep.id = tg.execution_plan_id "
            "WHERE ep.id = $1", row["execution_plan_id"],
        )
    plan_binding: dict[int, Any] = {}
    if graph_row is not None:
        raw = graph_row["nodes"]
        gnodes = json.loads(raw) if isinstance(raw, str) else (raw or [])
        for gn in gnodes:
            if gn.get("implementation_id") is not None:
                plan_binding[gn.get("order")] = gn["implementation_id"]
    out = []
    for n in nodes:
        d = dict(n)
        if d["implementation_id"] is None and d["node_order"] in plan_binding:
            d["plan_implementation_id"] = plan_binding[d["node_order"]]
        d["first_pass_success"] = (d["status"] == "succeeded" and d["attempt_count"] <= 1)
        out.append(d)
    return {"run_id": str(run_id), "status": row["status"], "nodes": out}


# ---------------------------------------------------------------------------
# rebuild + context-free runner
# ---------------------------------------------------------------------------
async def _rebuild(pool: asyncpg.Pool, run_id: str):
    """(run_row, compiled_plan, deps, {node_order: execution_run_nodes row}).
    Raises `DurableRunError` if the run or its plan is missing."""
    run_row = await _run_row(pool, run_id)
    if run_row is None:
        raise _dr.DurableRunError(f"execution_run {run_id} not found")
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT * FROM execution_plans WHERE id = $1", run_row["execution_plan_id"],
        )
        node_rows = {
            r["node_order"]: dict(r)
            for r in await conn.fetch(
                "SELECT * FROM execution_run_nodes WHERE execution_run_id = $1 ORDER BY node_order",
                run_id,
            )
        }
    if plan_row is None:
        raise _dr.DurableRunError(
            f"execution_run {run_id} references execution_plan "
            f"{run_row['execution_plan_id']} which does not exist"
        )
    compiled = await _row_to_compiled_plan(pool, plan_row)
    deps = {n.order: list(n.deps) for n in compiled.graph.nodes}
    # A node present in the run but absent from the rebuilt graph (graph
    # persisted with an empty nodes array) gets an empty dep list -- honest.
    for order in node_rows:
        deps.setdefault(order, [])
    return run_row, compiled, deps, node_rows


async def _blocking_reason(
    pool: asyncpg.Pool, compiled, node_rows: dict[int, dict],
) -> Optional[str]:
    """Return a human string if this run cannot be executed context-free
    (coding-agent plan, or an unfinished node with no real provider), else
    None."""
    if _is_coding_agent_plan(compiled.plan.extractor_version):
        return f"extractor_version={compiled.plan.extractor_version!r} is a coding-agent plan"

    by_order = {n.order: n for n in compiled.graph.nodes}
    for order, rn in sorted(node_rows.items()):
        if rn["status"] in ("succeeded", "cancelled"):
            continue
        node = by_order.get(order)
        impl_id = rn.get("implementation_id") or (
            node.implementation_id if node is not None else None
        )
        if impl_id is None:
            return f"node {order} has no durable implementation binding"
        from app.execution import implementation_registry

        impl = await implementation_registry.get(
            pool, str(impl_id), scope=AccessScope.unrestricted(),
        )
        if impl is None:
            return f"node {order} implementation {impl_id} does not resolve"
        if providers.get_provider(impl["kind"]) is None:
            return f"node {order} implementation kind {impl['kind']!r} has no provider"
    return None


def _make_runner(
    pool: asyncpg.Pool, compiled, run_row: asyncpg.Record,
) -> Callable[[int, int], Awaitable[dict[str, Any]]]:
    by_order = {n.order: n for n in compiled.graph.nodes}
    raw = run_row["parameters"]
    run_params = json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def _run_node(node_order: int, attempt: int) -> dict[str, Any]:
        node = by_order.get(node_order)
        if node is None:
            raise _dr.DurableRunError(
                f"node order {node_order} is not in the rebuilt graph -- "
                "cannot execute it context-free"
            )
        context = dict(run_params)
        context.update(getattr(node, "parameters", {}) or {})
        result = await execute_implementation(
            pool, node, context, scope=AccessScope.unrestricted(),
        )
        if getattr(result, "status", None) == "success":
            return {
                "notes": getattr(result, "notes", None),
                "data": dict(getattr(result, "data", {}) or {}),
                "attempt": attempt,
            }
        raise RuntimeError(
            f"node {node_order} reported failure: {getattr(result, 'notes', '')!r}"
        )

    return _run_node


# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------
async def resume_run_by_id(
    pool: asyncpg.Pool, run_id: str, *, worker_id: str, actor_id: Optional[str],
) -> dict[str, Any]:
    """Resume an eligible run through the real durable-run service.

    - not your run              -> raises `NotYourRun`
    - coding-agent / no-provider -> `{"status": "needs_product_context", ...}`
    - already terminal          -> `durable_run`'s idempotent no-op result
    - otherwise                 -> drives it and returns the run result
    """
    run_row, compiled, deps, node_rows = await _rebuild(pool, run_id)
    authorize_run_mutation(run_row["created_by"], actor_id)

    reason = await _blocking_reason(pool, compiled, node_rows)
    if reason is not None:
        out = _needs_product_context(run_id)
        out["reason"] = reason
        return out

    run_node = _make_runner(pool, compiled, run_row)
    return await _dr.resume_run(
        pool, run_id, deps=deps, run_node=run_node,
        worker_id=worker_id, compiled=compiled,
    )


async def retry_run_node_by_id(
    pool: asyncpg.Pool, run_id: str, node_order: int, *,
    worker_id: str, actor_id: Optional[str], force: bool = False,
) -> dict[str, Any]:
    """Explicit bounded retry of ONE failed / resumable / blocked node
    through `durable_run.retry_node`. Same auth + needs-product-context
    gates as `resume_run_by_id`. `force=True` bumps that node's
    `max_attempts` by 1 (operator override for an exhausted node). A
    succeeded node is never retried -- `durable_run` returns "already
    succeeded -- terminal".
    """
    run_row, compiled, deps, node_rows = await _rebuild(pool, run_id)
    authorize_run_mutation(run_row["created_by"], actor_id)

    if node_order not in node_rows:
        raise _dr.DurableRunError(f"node {node_order} is not in run {run_id}")

    reason = await _blocking_reason(pool, compiled, node_rows)
    if reason is not None:
        out = _needs_product_context(run_id)
        out["reason"] = reason
        return out

    run_node = _make_runner(pool, compiled, run_row)
    return await _dr.retry_node(
        pool, run_id, node_order, deps=deps, run_node=run_node,
        worker_id=worker_id, compiled=compiled, force=force,
    )
