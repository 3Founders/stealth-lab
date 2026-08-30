"""
Proving tests for app/execution/graph_executor.py -- the scheduler that
did not exist anywhere in this codebase after HTNAgent (the only prior
code that walked a multi-node dependency graph) was deleted.

REALISTIC SCENARIO, continuing the same real one from
test_plan_persistence_offline.py: fixing a pandas-upgrade break where
`DataFrame.append()` was removed, decomposed into the three real steps
that fix actually takes -- not a synthetic "node A, node B" fixture:

    0. grep the repo for remaining `.append(` calls on DataFrame objects
    1. rewrite each call site to `pd.concat([...])`          (deps=[0])
    2. run the test suite to confirm the migration is complete (deps=[1])

Plus a genuinely independent second branch to prove failure containment --
updating the changelog does not depend on the code fix at all:

    3. add a CHANGELOG entry noting the pandas migration    (deps=[])

Fully offline -- no DB, no real agent; `run_node` is a small fake that
records which nodes it was actually asked to run.
"""
from __future__ import annotations

import pytest

from app.execution.graph_executor import (
    GraphExecutionError,
    NodeResult,
    execute_task_graph,
)
from app.models.plan import PlanNode, TaskGraph
from uuid import uuid4


def _graph(nodes: list[PlanNode]) -> TaskGraph:
    return TaskGraph(execution_plan_id=uuid4(), graph_hash="test", nodes=nodes)


PANDAS_MIGRATION_NODES = [
    PlanNode(order=0, goal="grep the repo for remaining DataFrame.append( calls", deps=[]),
    PlanNode(order=1, goal="rewrite each call site to pd.concat([...])", deps=[0]),
    PlanNode(order=2, goal="run the test suite to confirm the migration is complete", deps=[1]),
    PlanNode(order=3, goal="add a CHANGELOG entry noting the pandas migration", deps=[]),
]


def _recording_runner(outcomes: dict[int, str]):
    """outcomes: {order: "success"|"failure"} for nodes that should
    actually be attempted; a node not in the dict but reached by the
    scheduler is a test bug, not a skip -- fails loudly via KeyError."""
    attempted: list[int] = []

    async def run_node(node: PlanNode) -> NodeResult:
        attempted.append(node.order)
        return NodeResult(status=outcomes[node.order], notes=f"ran node {node.order}")

    return run_node, attempted


@pytest.mark.asyncio
async def test_all_success_runs_every_node_in_dependency_order():
    run_node, attempted = _recording_runner({0: "success", 1: "success", 2: "success", 3: "success"})

    result = await execute_task_graph(_graph(PANDAS_MIGRATION_NODES), run_node=run_node)

    assert result.outcome == "success"
    assert set(attempted) == {0, 1, 2, 3}
    # 0 must be attempted before 1, and 1 before 2 -- the real dependency
    # chain this fix actually has.
    assert attempted.index(0) < attempted.index(1) < attempted.index(2)
    assert all(s == "success" for s in result.node_statuses.values())


@pytest.mark.asyncio
async def test_a_failed_node_blocks_only_its_transitive_dependents():
    """The real, measured property this module exists to preserve: node 1
    failing (the rewrite step) must block node 2 (tests, which depend on
    the rewrite) but must NOT block node 3 (the changelog entry, which
    depends on nothing) -- "the plan failed" and "one branch failed" stay
    different outcomes."""
    run_node, attempted = _recording_runner({0: "success", 1: "failure", 3: "success"})

    result = await execute_task_graph(_graph(PANDAS_MIGRATION_NODES), run_node=run_node)

    assert result.node_statuses[0] == "success"
    assert result.node_statuses[1] == "failure"
    assert result.node_statuses[2] == "skipped", (
        "node 2 depends on the failed node 1 and must never be attempted"
    )
    assert result.node_statuses[3] == "success", (
        "node 3 has no dependency on the failed branch and must still run"
    )
    assert 2 not in attempted, "a skipped node must never reach run_node at all"
    assert result.outcome == "needs_rework", (
        "partial success (3 landed) must not be reported as a flat failure -- "
        "real completed work should not be discarded"
    )


@pytest.mark.asyncio
async def test_all_failure_reports_failure_not_needs_rework():
    run_node, _ = _recording_runner({0: "failure", 3: "failure"})
    result = await execute_task_graph(_graph(PANDAS_MIGRATION_NODES), run_node=run_node)
    assert result.outcome == "failure"
    assert result.node_statuses[1] == "skipped"
    assert result.node_statuses[2] == "skipped"


@pytest.mark.asyncio
async def test_single_node_graph_is_the_common_case_and_just_works():
    """find_best_way's tier-2 compiles exactly this shape today (one node
    per flat-agent run) -- this executor must handle it as a strict
    special case, not a separate path."""
    node = PlanNode(order=0, goal="fix the bug", deps=[])
    run_node, attempted = _recording_runner({0: "success"})

    result = await execute_task_graph(_graph([node]), run_node=run_node)

    assert result.outcome == "success"
    assert attempted == [0]


@pytest.mark.asyncio
async def test_empty_graph_is_refused_not_silently_a_no_op():
    async def run_node(node):  # pragma: no cover -- must never be called
        raise AssertionError("should never be reached")

    with pytest.raises(GraphExecutionError):
        await execute_task_graph(_graph([]), run_node=run_node)


@pytest.mark.asyncio
async def test_a_dependency_outside_the_graph_is_refused_loudly():
    """Defense in depth: plans.py's validate_graph should have already
    caught this at compile time, but the executor must not silently
    misbehave (e.g. treat a missing dep as already-satisfied) if it is
    ever handed an unvalidated graph directly."""
    bad_node = PlanNode(order=0, goal="depends on nothing real", deps=[99])

    async def run_node(node):  # pragma: no cover
        raise AssertionError("should never be reached")

    with pytest.raises(GraphExecutionError):
        await execute_task_graph(_graph([bad_node]), run_node=run_node)
