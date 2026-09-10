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
# report_node_progress_by_id (MCP hardening B6): the context-free,
# auth-gated entrypoint over durable_run.report_node_progress -- the ONE
# host-executed-lease progress-reporting mutation. Same ownership check
# every other mutation in this module already uses; no rebuild of a
# runnable CompiledPlan is needed here (nothing is being DRIVEN, only a
# real, already-observed outcome recorded), unlike resume/retry.
# ---------------------------------------------------------------------------
async def report_node_progress_by_id(
    pool: asyncpg.Pool, run_id: str, node_order: int, *, actor_id: Optional[str],
    ok: bool, result: Optional[dict[str, Any]] = None,
    error_class: Optional[str] = None, error: Optional[dict[str, Any]] = None,
    worker_id: Optional[str] = None,
) -> dict[str, Any]:
    row = await _run_row(pool, run_id)
    if row is None:
        raise _dr.DurableRunError(f"execution_run {run_id} not found")
    authorize_run_mutation(row["created_by"], actor_id)
    return await _dr.report_node_progress(
        pool, run_id, node_order, ok=ok, result=result,
        error_class=error_class, error=error, worker_id=worker_id,
    )


# ---------------------------------------------------------------------------
# get_run_context (MCP hardening B4/B32): the smallest useful next-action
# packet, anchored to the run's PINNED Procedure version -- "continue_run"'s
# actual assembly. Deliberately built from existing reads (this function's
# own siblings above, fetch_procedure_version, project_state) rather than a
# new retrieval mechanism, per CLAUDE.md rule 2.
# ---------------------------------------------------------------------------
async def get_run_context(pool: asyncpg.Pool, run_id: str) -> Optional[dict[str, Any]]:
    """
    B4's `continue_run` packet: loads the exact pinned Procedure version,
    current PlanNode/run state, and a bounded precondition-derived Claim
    working set, then returns the smallest useful next-action packet --
    never the whole Procedure or Claim corpus (B30's own rule, honored
    here even though the full relevant-Claim-working-set service itself
    is a separate, later gate).

    `None` when the run does not exist.
    """
    from app.execution.procedure_graph import fetch_procedure_version
    from app.services.route_decision import classify_precondition_gap
    from app.services.state import project_state

    run = await _run_row(pool, run_id)
    if run is None:
        return None

    procedure = await fetch_procedure_version(pool, run["procedure_id"], run["procedure_version"])

    async with pool.acquire() as conn:
        graph_row = await conn.fetchrow(
            "SELECT tg.nodes FROM task_graphs tg WHERE tg.id = $1", run["task_graph_id"],
        )
        node_rows = await conn.fetch(
            "SELECT id, node_order, status, attempt_count, max_attempts, error_class, "
            " implementation_id, implementation_version, verification_state "
            "FROM execution_run_nodes WHERE execution_run_id = $1 ORDER BY node_order",
            run_id,
        )
    plan_nodes: dict[int, dict] = {}
    if graph_row is not None:
        raw = graph_row["nodes"]
        for gn in (json.loads(raw) if isinstance(raw, str) else (raw or [])):
            plan_nodes[gn.get("order")] = gn

    nodes = [dict(n) for n in node_rows]
    for n in nodes:
        plan_node = plan_nodes.get(n["node_order"], {})
        n["goal"] = plan_node.get("goal")
        n["deps"] = plan_node.get("deps") or []

    NON_TERMINAL = ("pending", "running", "resumable", "blocked")
    current = next((n for n in nodes if n["status"] in NON_TERMINAL), None)

    # Preconditions: TRUE/FALSE/UNKNOWN, never collapsed (B27) -- reuses
    # the exact UNKNOWN-vs-FALSE distinction route_decision.py's
    # classify_precondition_gap already established for the router, so
    # this and the router never disagree about what "unknown" means.
    as_of = run["started_at"] or run["created_at"]
    access_scope = AccessScope.for_user(run["created_by"]) if run["created_by"] else AccessScope.unrestricted()
    required_preconditions: list[dict] = []
    blocking_unknowns: list[dict] = []
    for precondition in (procedure or {}).get("preconditions") or []:
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        if not subject:
            continue
        claims = await project_state(pool, subjects=[subject], as_of=as_of, scope=access_scope)
        satisfied = any(
            c["properties"].get("predicate") == predicate
            and c["properties"].get("object") == expected_object
            for c in claims
        )
        if satisfied:
            truth = "TRUE"
        elif not claims:
            truth = "UNKNOWN"
        else:
            truth = "FALSE"
        entry = {"subject": subject, "predicate": predicate, "object": expected_object, "status": truth}
        required_preconditions.append(entry)
        if truth == "UNKNOWN":
            blocking_unknowns.append(entry)

    # Implementation options for the CURRENT node only (bounded, not the
    # whole registry): per-node binding first (execution_run_nodes /
    # compiled-plan hint), else the registry's own task-linked candidates
    # when this procedure has a real migrated_from_task_node_id -- never
    # an invented Implementation when neither exists (B23).
    recommended_implementations: list[dict] = []
    if current is not None:
        if current.get("implementation_id"):
            recommended_implementations.append({
                "implementation_id": str(current["implementation_id"]),
                "implementation_version": current.get("implementation_version"),
                "source": "pinned_on_node",
            })
        else:
            plan_impl = plan_nodes.get(current["node_order"], {}).get("implementation_id")
            if plan_impl:
                recommended_implementations.append(
                    {"implementation_id": str(plan_impl), "source": "compiled_plan_hint"}
                )
            elif procedure is not None:
                # B23/B24: the real Procedure<->Implementation relation
                # (migration 53) is the preferred resolution source now
                # that it exists -- checked before the legacy task-node
                # registry path, never instead of it (a procedure minted
                # before this relation existed still resolves via its old
                # migrated_from_task_node_id link, per CLAUDE.md rule 6:
                # preserve compatibility paths until replacements are
                # proven, don't rip out the old path on day one).
                from app.services.procedure_implementation_bindings import (
                    get_bindings_for_procedure,
                )
                bindings = await get_bindings_for_procedure(
                    pool, procedure_id=procedure["procedure_id"],
                    status="active", access_scope=access_scope,
                )
                step_bindings = [
                    b for b in bindings
                    if not b["supported_steps"] or current["node_order"] in b["supported_steps"]
                ]
                if step_bindings:
                    recommended_implementations = [
                        {
                            "implementation_id": str(b["implementation_id"]),
                            "role": b["role"], "source": "procedure_implementation_binding",
                        }
                        for b in step_bindings
                    ]
                elif procedure.get("migrated_from_task_node_id"):
                    from app.execution import implementation_registry as _impl_registry
                    registry_hits = await _impl_registry.get_for_task(
                        pool, str(procedure["migrated_from_task_node_id"]),
                        scope=access_scope, status="active",
                    )
                    recommended_implementations = [
                        {"implementation_id": str(h["id"]), "kind": h.get("kind"), "source": "registry"}
                        for h in registry_hits
                    ]

    # B9-B13: is the current node actively waiting on a live child run
    # right now? Derived, not stored (migration 52's own rationale) --
    # a query over execution_runs.parent_run_id/parent_node_id, never a
    # separate "WAITING_CHILD" status value.
    waiting_child = None
    if current is not None:
        from app.execution.recursion_guard import describe_child_status
        waiting_child = await describe_child_status(
            pool, run_id=run_id, node_row_id=str(current["id"]),
        )

    if current is not None and waiting_child is not None:
        phase = f"node:{current['node_order']}:waiting_child"
        objective = current.get("goal")
        next_when_satisfied = (
            f"the child run {waiting_child['child_run_id']} (status="
            f"{waiting_child['child_status']}) must reach a terminal state; "
            "call continue_run on the CHILD to see its own next action, or "
            "poll this continue_run again once it terminates"
        )
    elif current is not None:
        phase = f"node:{current['node_order']}"
        objective = current.get("goal")
        next_when_satisfied = (
            f"report progress for node {current['node_order']} (report_execution / "
            "retry_run_node on failure), then call continue_run again"
        )
    elif run["status"] == "succeeded":
        phase, objective = "complete", None
        next_when_satisfied = "call verify_completion, then report_execution to finalize"
    else:
        phase, objective = run["status"], None
        next_when_satisfied = "no further node is runnable -- inspect_run for the failure/blocking detail"

    # B1/B32: real relevant-Claims retrieval (get_relevant_claims, B30),
    # bounded, keyed on the current node's own goal when there is one
    # (falls back to the procedure's own goal for a terminal/no-current-
    # node run) -- NOT a reuse of required_preconditions (that was a
    # placeholder; preconditions and "claims relevant to what I'm doing
    # right now" are different bounded sets, per B36's own text: "Claims
    # = what is believed/known" is distinct from a run's precondition
    # checklist).
    relevant_claim_refs: list[dict] = []
    claims_query = (objective if current is not None else None) or (procedure or {}).get("goal")
    if claims_query:
        from app.services.relevant_claims import get_relevant_claims
        try:
            relevant_claim_refs = await get_relevant_claims(
                pool, goal=claims_query, top_k=5, access_scope=access_scope,
            )
        except Exception:  # noqa: BLE001 -- informational; must never break continue_run itself.
            relevant_claim_refs = []

    return {
        "procedure_run_id": str(run_id),
        "procedure_id": str(run["procedure_id"]),
        "procedure_version": run["procedure_version"],
        "route_decision_id": str(run["route_decision_id"]) if run.get("route_decision_id") else None,
        "parent_run_id": str(run["parent_run_id"]) if run.get("parent_run_id") else None,
        "root_run_id": str(run["root_run_id"]) if run.get("root_run_id") else None,
        "status": run["status"],
        "current_phase_or_node": phase,
        "objective": objective,
        "waiting_child": waiting_child,
        "required_preconditions": required_preconditions,
        "relevant_claim_refs": relevant_claim_refs,
        "recommended_implementations": recommended_implementations,
        "required_checks": (procedure or {}).get("postconditions") or [],
        "allowed_branches": [],  # honest: stored procedures have no branching field (db/18)
        "blocking_unknowns": blocking_unknowns,
        "next_when_satisfied": next_when_satisfied,
        "nodes": nodes,
    }


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
