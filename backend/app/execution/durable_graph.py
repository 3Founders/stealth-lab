"""
The one bridge from a compiled TaskGraph to a DURABLE execution run
(final-V1 §3). The production tier-2 execution path (MCP `find_best_way`,
`reproduce_procedure`) goes through here instead of the in-memory
`graph_executor.execute_task_graph`, so a crashed run can be resumed
through the same product path -- one authoritative durable execution
path, not two execution semantics.

`graph_executor` is unchanged and still used by callers that genuinely
want a single in-memory pass (offline tests, non-stateful helpers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app.execution import durable_run as _dr
from app.execution.graph_executor import NodeResult
from app.models.plan import ExecutionOutcome
from app.utils.ids import uuid7


@dataclass
class DurableGraphResult:
    """Shaped so `.outcome` is a drop-in for graph_executor's
    GraphExecutionResult at the existing call sites, plus the durable
    handles a caller now has (`run_id`, resume state)."""
    outcome: ExecutionOutcome
    run_id: str
    status: Optional[str]
    node_summary: list[dict[str, Any]] = field(default_factory=list)
    resume_count: int = 0
    final_execution_id: Optional[str] = None

# Caller supplies a factory that, given a PlanNode, returns the NodeResult
# for that node (raising is also allowed and is treated as failure).
MakeRunNode = Callable[[Any], Awaitable[NodeResult]]


def _deps_from_graph(graph) -> dict[int, list[int]]:
    return {n.order: list(n.deps) for n in graph.nodes}


def _outcome_from_run(result: dict[str, Any]) -> ExecutionOutcome:
    fo = result.get("final_outcome")
    if fo == "success":
        return "success"
    if fo == "needs_rework":
        return "needs_rework"
    # non-terminal (paused / still resumable) or explicit failure
    return "failure"


async def run_graph_durably(
    pool: asyncpg.Pool,
    compiled,
    node_runner: MakeRunNode,
    *,
    procedure_id: str,
    procedure_version: int,
    worker_id: Optional[str] = None,
    side_effecting_orders: Optional[set[int]] = None,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    max_attempts: int = 3,
    resume_run_id: Optional[str] = None,
    # MCP hardening B3: ProcedureRun identity (migration 51), forwarded to
    # start_run() unchanged. Ignored when resuming an existing run (its
    # identity was already fixed at creation) -- only meaningful on the
    # `resume_run_id=None` (fresh-run) branch.
    request_id: Optional[str] = None, workspace_id: Optional[str] = None,
    trace_id: Optional[str] = None, parent_run_id: Optional[str] = None,
    parent_node_id: Optional[str] = None,
    claim_working_set_revision: Optional[datetime] = None,
    route_decision_id: Optional[str] = None,
) -> "DurableGraphResult":
    """
    Execute (or resume) `compiled`'s graph as a durable run.

    - `resume_run_id=None`  -> start a fresh execution_run, then execute_run().
    - `resume_run_id=<id>`  -> resume_run() that existing run with a
      freshly-rebuilt `node_runner` (the product-path resume, §3).

    On terminal, durable_run appends the one immutable `executions` row
    (via record_plan_execution, implementation_id pinned) -- callers must
    NOT also call record_plan_execution.

    Returns {run_id, outcome (ExecutionOutcome), status, node_summary,
    resume_count, final_execution_id}.
    """
    graph = compiled.graph
    deps = _deps_from_graph(graph)
    by_order = {n.order: n for n in graph.nodes}
    worker_id = worker_id or f"tier2-{uuid7().hex[:8]}"

    async def _cb(node_order: int, attempt: int) -> dict[str, Any]:
        node = by_order[node_order]
        try:
            res = await node_runner(node)
        except BaseException:  # noqa: BLE001 -- durable_run classifies + records it
            raise
        if getattr(res, "status", None) == "success":
            return {"notes": getattr(res, "notes", None), "data": dict(getattr(res, "data", {}) or {}),
                    "attempt": attempt}
        # a NodeResult(status="failure") -> raise so durable_run records it
        raise RuntimeError(f"node {node_order} reported failure: {getattr(res, 'notes', '')!r}")

    if resume_run_id is None:
        run_id = await _dr.start_run(
            pool,
            execution_plan_id=str(compiled.plan.id),
            task_graph_id=str(compiled.graph.id),
            procedure_id=str(procedure_id),
            procedure_version=int(procedure_version),
            node_orders=list(by_order.keys()),
            deps=deps,
            side_effecting=side_effecting_orders or set(),
            max_attempts=max_attempts,
            created_by=created_by,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            request_id=request_id, workspace_id=workspace_id, trace_id=trace_id,
            parent_run_id=parent_run_id, parent_node_id=parent_node_id,
            claim_working_set_revision=claim_working_set_revision,
            route_decision_id=route_decision_id,
        )
        result = await _dr.execute_run(
            pool, run_id, deps=deps, run_node=_cb, worker_id=worker_id, compiled=compiled,
        )
    else:
        run_id = str(resume_run_id)
        result = await _dr.resume_run(
            pool, run_id, deps=deps, run_node=_cb, worker_id=worker_id, compiled=compiled,
        )

    return DurableGraphResult(
        outcome=_outcome_from_run(result),
        run_id=run_id,
        status=result.get("status"),
        node_summary=result.get("nodes", []),
        resume_count=result.get("resume_count", 0),
        final_execution_id=result.get("final_execution_id"),
    )


async def create_pending_run(
    pool: asyncpg.Pool,
    compiled,
    *,
    procedure_id: str,
    procedure_version: int,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    max_attempts: int = 3,
    request_id: Optional[str] = None, workspace_id: Optional[str] = None,
    trace_id: Optional[str] = None, parent_run_id: Optional[str] = None,
    parent_node_id: Optional[str] = None,
    claim_working_set_revision: Optional[datetime] = None,
    route_decision_id: Optional[str] = None,
) -> str:
    """
    MCP hardening B3: a real, durable `procedure_run_id` for a Procedure
    that was ACCEPTED FOR USE but not (yet, or ever, for `assist`) driven
    -- `find_best_way`'s assist/plan_ready routes. Same `start_run()` this
    module's own `run_graph_durably()` uses for the tier-2/execute path,
    just without the matching `execute_run()` call -- the run stays
    `pending`, exactly the state a caller-driven `continue_run()` (B4)
    expects to find and hand back a next-action packet for.

    Deliberately NOT a new mechanism: same execution_runs/execution_run_nodes
    rows, same idempotent request_id semantics, same terminal-state fence.
    A caller may later drive this exact run_id through `execute_run`/
    `resume_run` (via `run_graph_durably(..., resume_run_id=run_id)`) if a
    plan_ready/assist decision later turns into a real execution -- no
    second run is created for that continuation.
    """
    graph = compiled.graph
    deps = _deps_from_graph(graph)
    return await _dr.start_run(
        pool,
        execution_plan_id=str(compiled.plan.id),
        task_graph_id=str(compiled.graph.id),
        procedure_id=str(procedure_id),
        procedure_version=int(procedure_version),
        node_orders=[n.order for n in graph.nodes],
        deps=deps,
        max_attempts=max_attempts,
        created_by=created_by,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        request_id=request_id, workspace_id=workspace_id, trace_id=trace_id,
        parent_run_id=parent_run_id, parent_node_id=parent_node_id,
        claim_working_set_revision=claim_working_set_revision,
        route_decision_id=route_decision_id,
    )
