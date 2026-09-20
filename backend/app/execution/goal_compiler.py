"""
Deterministic DAG flattening over a resolved Goal tree (execu.md Sec
16-18): the compiler half of the product loop, deliberately separate
from `goal_resolution.py`'s own recursive SELECTION logic (Sec 10: "the
ranker selects Implementations, the compiler owns graph structure" --
the same separation `app/execution/plans.py::compile_plan` already
holds for Procedure-based plans, applied here to Goal-based ones).

Pure and pool-free (same discipline `compile_plan()` holds, for the same
reason: its contracts must be provable offline). Takes an
already-resolved `ResolvedGoalNode` tree (goal_resolution.resolve_goal's
output) and flattens it into an ordered list of concrete nodes:

  - one concrete node per `chosen == "step"` LEAF (a procedure step carrying a
    `binding`) -- the real work.
  - one concrete HUMAN node per `chosen == "unresolved"` leaf (Sec 4/21:
    "A Goal may initially be unsolved" is a real, durable state, not an
    error to hide -- surfaced here as an explicit NEEDS_INPUT node a
    human can act on, never silently dropped from the DAG).
  - a `chosen == "procedure"` node itself emits NOTHING -- it is pure
    decomposition (Sec 2: "Procedure: decomposition of one Goal into
    child Goals"), not executable work. Its children's own nodes become
    the real DAG, chained in the Procedure's own step order (steps are
    linear by construction -- db/18_procedures.sql's own DDL comment,
    the same fact `procedure_graph.py::steps_to_linear_nodes` already
    relies on) -- one subtree's LAST concrete node becomes the next
    subtree's dependency.

Cardinality falls out naturally: a one-step procedure is one node; a
procedure whose every step is bound is one node per step; a procedure with an
unresolved step yields a human node for that step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from app.execution.goal_resolution import ResolvedGoalNode


@dataclass
class GoalPlanNode:
    """One concrete, executable (or human-actionable) node of a
    flattened Goal DAG. `kind='human'` nodes are real, durable NEEDS_INPUT
    state (Sec 21), not a placeholder -- `rationale` always says exactly
    what could not be resolved and why."""

    node_id: str
    goal_id: str
    goal_name: str
    kind: Literal["step", "human"]
    step_order: Optional[int] = None
    procedure_id: Optional[str] = None
    executor: str = "human"
    deps: list[str] = field(default_factory=list)
    rationale: str = ""
    depth: int = 0


def flatten_goal_tree(tree: ResolvedGoalNode) -> list[GoalPlanNode]:
    """The real Sec 16 flattening: walks `tree` depth-first, in the exact
    order `resolve_goal()` itself resolved children (a Procedure's own
    step order), emitting one `GoalPlanNode` per leaf. Deterministic --
    same tree in, same node list out, always; no ranking, no selection
    happens here (that already happened inside `resolve_goal`, this
    function only linearizes its result).
    """
    nodes: list[GoalPlanNode] = []
    counter = [0]

    def _next_id() -> str:
        nid = f"N{counter[0]}"
        counter[0] += 1
        return nid

    def walk(node: ResolvedGoalNode, prior_dep: Optional[str]) -> Optional[str]:
        """Returns the node_id of the LAST concrete node emitted for this
        subtree (the dependency the NEXT sibling subtree should chain
        onto), or None if this subtree emitted nothing concrete (cannot
        happen today -- every branch emits exactly one node or recurses
        into children that do -- kept as a real return type rather than
        assumed, so a future branch that legitimately emits nothing
        doesn't silently break the chain)."""
        if node.chosen == "step":
            from app.execution.step_binding import executor_kind
            nid = _next_id()
            nodes.append(GoalPlanNode(
                node_id=nid, goal_id=node.goal_id, goal_name=node.goal_name,
                kind="step", step_order=(node.step or {}).get("order"), procedure_id=(node.step or {}).get("procedure_id"),
                executor=executor_kind((node.step or {}).get("binding")),
                deps=[prior_dep] if prior_dep else [],
                rationale=node.rationale, depth=node.depth,
            ))
            return nid

        if node.chosen == "unresolved":
            nid = _next_id()
            nodes.append(GoalPlanNode(
                node_id=nid, goal_id=node.goal_id, goal_name=node.goal_name,
                kind="human", executor="human",
                deps=[prior_dep] if prior_dep else [],
                rationale=node.unresolved_reason or "unresolved",
                depth=node.depth,
            ))
            return nid

        if node.chosen == "procedure":
            last = prior_dep
            for child in node.children:
                emitted = walk(child, last)
                if emitted is not None:
                    last = emitted
            return last

        raise AssertionError(f"unknown ResolvedGoalNode.chosen value: {node.chosen!r}")

    walk(tree, None)
    return nodes


def compiled_goal_to_run_md(nodes: list[GoalPlanNode]) -> str:
    """Prompt 2 Sec 13, the COMPILE-time half: `find_best_way(mode=
    "plan_only")` already writes a real `run.md` for a Procedure-based
    plan before anything executes (`RUN|...|pending|...`) -- this is the
    same honesty for a Goal-based plan, via `compile_goal` (pure, no
    pool, no execution -- see this module's own docstring). Every node's
    `status` is `"planned"` (a real bound step WOULD be dispatched
    here) or `"needs_input"` (a real, already-known gap -- Sec 4/21's
    own `human` kind) -- never `"success"`/`"failure"`, since nothing
    has actually run yet. `execution_id='-'` (pipe_format.py's own
    documented convention for a non-durable, inspection-only render) --
    a compile has no real execution attempt to name.

    Reuses `pipe_format.py::GoalRunLine`/`render_goal_run_md` verbatim --
    the SAME real grammar `goal_execution.py`'s own execute-time writer
    produces, not a second one; a caller `rg`-ing `goal_run.md` sees one
    consistent format whether a run was only planned or actually executed.
    """
    from app.stealth.pipe_format import GoalRunLine, render_goal_run_md

    lines = [
        GoalRunLine(
            goal_id=n.goal_id, kind=n.kind,
            status="planned" if n.kind == "step" else "needs_input",
        )
        for n in nodes
    ]
    return render_goal_run_md("-", "planned", lines)
