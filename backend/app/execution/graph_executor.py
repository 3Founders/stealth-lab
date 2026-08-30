"""
The real scheduler for a compiled TaskGraph -- the piece schema.md's own
line ("TaskGraph is the only place scheduling is legal") calls for, and
which genuinely did not exist anywhere after HTNAgent (the only code that
ever walked a multi-node dependency graph node-by-node) was deleted from
this project. `app/execution/plans.py` compiles a graph and validates its
shape (acyclic, resolvable deps); `app/execution/plan_persistence.py`
writes it to real storage. Neither one EXECUTES it. This module is that
missing third piece.

Design choice, and why: dependency-injected node execution
(`run_node: PlanNode -> NodeResult`), not a hardcoded call to any specific
agent. `plans.py` stays pure/pool-free on purpose (provable offline); this
module keeps the same discipline for the same reason, plus a second one --
HTNAgent's deletion is exactly the lesson that hardwiring one execution
mechanism into the scheduler makes the scheduler die with the mechanism.
Whatever performs a node's actual work (today's flat `Agent`, a future
replacement, a human-in-the-loop approval step) is the caller's concern;
this module only owns ordering, blocking, and aggregation.

Failure containment, preserved from the real, measured property HTNAgent's
own (now-deleted) docstring justified with real data: a failed node blocks
only its TRANSITIVE DEPENDENTS. A node whose dependencies are unaffected by
a failure elsewhere still runs -- "the plan failed" and "one branch failed"
are different outcomes, and collapsing that distinction would be a real
regression, not a simplification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

from app.models.plan import ExecutionOutcome, PlanNode, TaskGraph

NodeStatus = Literal["success", "failure", "skipped"]


@dataclass(frozen=True)
class NodeResult:
    """What a `run_node` callback reports back for one node it actually
    attempted. `skipped` nodes (below) never call it at all -- there is
    nothing for the caller to report on a node that never ran."""

    status: Literal["success", "failure"]
    notes: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphExecutionResult:
    outcome: ExecutionOutcome
    node_statuses: dict[int, NodeStatus]  # keyed by PlanNode.order
    node_results: dict[int, NodeResult]   # only entries for nodes actually run


class GraphExecutionError(ValueError):
    """The graph itself isn't executable as given -- a caller bug (e.g. an
    unvalidated graph), not a node failure. Real node failures are a normal
    result (`GraphExecutionResult`), not an exception."""


def _topological_batches(nodes: list[PlanNode]) -> list[list[PlanNode]]:
    """Kahn's algorithm, batched by dependency depth rather than flattened
    to one order: nodes with no unmet dependency, as a group, are
    independent of each other and belong in the same batch -- this is what
    lets two unrelated branches both be attempted even after one of them
    contains a failure elsewhere. Assumes the graph is already validated
    acyclic (`plans.py::validate_graph` does this at compile time); this
    function does not re-validate, only orders."""
    by_order = {n.order: n for n in nodes}
    indegree = {n.order: len(n.deps) for n in nodes}
    dependents: dict[int, list[int]] = {n.order: [] for n in nodes}
    for n in nodes:
        for d in n.deps:
            if d not in by_order:
                raise GraphExecutionError(
                    f"node {n.order} depends on {d}, which is not in this graph "
                    "-- graph was not validated before reaching the executor"
                )
            dependents[d].append(n.order)

    batches: list[list[PlanNode]] = []
    remaining = dict(indegree)
    scheduled: set[int] = set()
    while len(scheduled) < len(nodes):
        ready = [o for o, deg in remaining.items() if deg == 0 and o not in scheduled]
        if not ready:
            stranded = sorted(set(remaining) - scheduled)
            raise GraphExecutionError(
                f"node(s) {stranded} are unreachable -- graph is not a DAG "
                "(should have been caught at compile time)"
            )
        batches.append([by_order[o] for o in sorted(ready)])
        for o in ready:
            scheduled.add(o)
            for dep in dependents[o]:
                remaining[dep] -= 1
    return batches


async def execute_task_graph(
    graph: TaskGraph,
    *,
    run_node: Callable[[PlanNode], Awaitable[NodeResult]],
) -> GraphExecutionResult:
    """
    Execute every node in `graph`, respecting `deps`, blocking only a
    failed node's transitive dependents.

    A single-node graph (today's common case -- see find_best_way's tier-2,
    which compiles one node per flat-agent run) executes as one batch of
    one, so this is a strict superset of that behavior, not a parallel
    path: nothing about find_best_way's current single-node plans needs to
    change for this module to already apply to them correctly.
    """
    nodes = list(graph.nodes)
    if not nodes:
        raise GraphExecutionError("a graph with no nodes cannot be executed")

    batches = _topological_batches(nodes)
    statuses: dict[int, NodeStatus] = {}
    results: dict[int, NodeResult] = {}

    for batch in batches:
        for node in batch:
            blocked_by = [d for d in node.deps if statuses.get(d) in ("failure", "skipped")]
            if blocked_by:
                statuses[node.order] = "skipped"
                continue
            result = await run_node(node)
            results[node.order] = result
            statuses[node.order] = result.status

    if all(s == "success" for s in statuses.values()):
        outcome: ExecutionOutcome = "success"
    elif any(s == "success" for s in statuses.values()):
        # Partial success -- some real work landed, some didn't. schema.md's
        # "needs_rework" is exactly this case, not a plain failure: a
        # completed branch's work should not be discarded by re-running
        # everything from scratch.
        outcome = "needs_rework"
    else:
        outcome = "failure"

    return GraphExecutionResult(outcome=outcome, node_statuses=statuses, node_results=results)
