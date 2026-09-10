"""
MCP hardening B17/B33: planned vs actual execution.

B17: "Persist separately: planned procedure/implementation/steps AND
actual procedure/version/implementation/steps/tools/artifacts/outcome.
This enables adherence, deviation, false-reuse, and failure analysis."

B33: "...detecting material deviation from the selected Procedure..."

Both name the SAME real capability this codebase's data already
supports but never derived: `task_graphs.nodes` (the COMPILED, i.e.
PLANNED, graph -- each node's `goal`/`implementation_id` hint, set once
at compile time, immutable per migration 23's own frozen-table
enforcement) versus `execution_run_nodes` (the ACTUAL, mutable,
per-attempt outcome -- status/implementation_id actually pinned/
attempt_count/error_class). No new persistence: "planned" and "actual"
are ALREADY persisted separately, in separate tables, by separate
writers (compile_plan vs durable_run's own node-claim/finish mechanics)
-- what was missing is comparing them.

WHY A DERIVED COMPARISON, NOT A NEW "deviation" COLUMN (CLAUDE.md rule
2): a stored deviation flag could only go stale the moment either side
changes; task_graphs is frozen (never changes) and execution_run_nodes
mutates through its own already-guarded transitions (migration 36's
terminal fence) -- a live comparison is always correct, a cached one
would need its own invalidation logic for no real benefit.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


async def compute_plan_deviation(pool: asyncpg.Pool, execution_run_id: str) -> Optional[dict[str, Any]]:
    """Returns None if the run does not exist. Otherwise:
    {"per_node": [...], "material_deviation": bool, "summary": {...}}

    `per_node` entries: {node_order, planned_goal, planned_implementation_id,
    actual_status, actual_implementation_id, attempt_count, error_class,
    deviations: [...named deviation strings for this node, empty if none]}.

    `material_deviation` is True iff ANY node has a non-empty
    `deviations` list -- "material" per B33's own word, meaning it
    actually diverged from the plan, not merely that the run isn't
    finished yet (a still-pending node with no attempts is not a
    deviation, it just hasn't run)."""
    run = await pool.fetchrow(
        "SELECT task_graph_id FROM execution_runs WHERE id = $1", execution_run_id,
    )
    if run is None:
        return None

    graph_row = await pool.fetchrow(
        "SELECT nodes FROM task_graphs WHERE id = $1", run["task_graph_id"],
    )
    planned_nodes: dict[int, dict] = {}
    if graph_row is not None:
        raw = graph_row["nodes"]
        for gn in (json.loads(raw) if isinstance(raw, str) else (raw or [])):
            order = gn.get("order")
            if order is not None:
                planned_nodes[order] = gn

    actual_rows = await pool.fetch(
        "SELECT node_order, status, implementation_id, attempt_count, error_class "
        "FROM execution_run_nodes WHERE execution_run_id = $1 ORDER BY node_order",
        execution_run_id,
    )

    per_node: list[dict[str, Any]] = []
    for row in actual_rows:
        order = row["node_order"]
        planned = planned_nodes.get(order, {})
        planned_impl = planned.get("implementation_id")
        actual_impl = str(row["implementation_id"]) if row["implementation_id"] else None

        deviations: list[str] = []
        if row["status"] == "failed":
            deviations.append("node_failed")
        if row["status"] == "blocked":
            deviations.append("node_blocked_by_upstream_failure")
        if row["attempt_count"] > 1:
            deviations.append("required_retry")
        if planned_impl and actual_impl and str(planned_impl) != actual_impl:
            deviations.append("implementation_diverged_from_plan")
        if order not in planned_nodes:
            deviations.append("actual_node_not_in_compiled_plan")

        per_node.append({
            "node_order": order,
            "planned_goal": planned.get("goal"),
            "planned_implementation_id": str(planned_impl) if planned_impl else None,
            "actual_status": row["status"],
            "actual_implementation_id": actual_impl,
            "attempt_count": row["attempt_count"],
            "error_class": row["error_class"],
            "deviations": deviations,
        })

    for order in planned_nodes:
        if order not in {n["node_order"] for n in per_node}:
            per_node.append({
                "node_order": order, "planned_goal": planned_nodes[order].get("goal"),
                "planned_implementation_id": None, "actual_status": None,
                "actual_implementation_id": None, "attempt_count": 0, "error_class": None,
                "deviations": ["planned_node_never_executed"],
            })
    per_node.sort(key=lambda n: n["node_order"])

    material_deviation = any(n["deviations"] for n in per_node)
    summary = {
        "nodes_planned": len(planned_nodes),
        "nodes_executed": len(actual_rows),
        "nodes_with_deviations": sum(1 for n in per_node if n["deviations"]),
    }

    return {"per_node": per_node, "material_deviation": material_deviation, "summary": summary}
