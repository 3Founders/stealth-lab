"""
Execute a resolved Goal route (Prompt 2 Sec 7/9/10, 2026-09-15) -- the
actual "execute" step of Sec 7's own compiler pipeline
(`resolve_goal -> ground_goal -> select_routes -> ... -> execute`).

Executes the concrete STEP leaves of an already-resolved Goal tree. A step is executable when it carries a
`binding` (see `step_binding.py`); there is no separate Implementation object -- "an implementation" is a
one-step procedure or a step of a multi-step one. Fallback is at Procedure level: when a whole Procedure's
walk fails, the next feasible alternate Procedure is resolved lazily and tried (Prompt 2 Sec 10).

SCOPE, STATED HONESTLY: `execution_runs`/`execution_plans` (migration 23/36) give full crash/resume durability
for the tier-2 Procedure pipeline (`compile_plan` -> `persist_compiled_plan` -> `durable_run.py`), which this
module does not replace. This module is a SEQUENTIAL walk over a Goal tree's step leaves, in the order
`resolve_goal` already sorted them (`graph_executor.py` owns parallel scheduling for the TaskGraph shape).

  DURABILITY UPDATE (Prompt 2 Sec 12, this pass): passing `workspace_root`
  (+ optionally `execution_id` to resume a SPECIFIC prior attempt) makes
  this walk resumable after a real process crash -- NOT by using the
  Postgres `execution_runs` machinery (still out of reach for the reason
  above), but by reusing `app.stealth.journal` -- the SAME real,
  already-tested, fsync'd, single-writer-locked local append-only
  `.stealth/events.jsonl` mechanism `generator.py` already uses for
  Procedure-run projections. Every node's real outcome is appended as a
  `goal_node_result` event; on a fresh call with the SAME `execution_id`,
  a node whose last recorded outcome was `status='success'` is never
  re-executed -- its real, previously-recorded result is reused instead
  (Sec 12's own rule: "a resumed run MUST continue from persisted
  execution state rather than reconstructing an inconsistent DAG"). A
  node with no prior record, or one whose last recorded outcome was
  `'failure'`, is always (re-)attempted for real -- resuming never
  silently treats an unfinished or failed node as done. This is
  DELIBERATELY narrower than full crash-resume durability (there is no
  lease/worker-ownership fencing here, so two concurrent resumes of the
  same `execution_id` could both attempt the same not-yet-recorded node
  -- a real, disclosed gap, not a false claim of the same
  concurrent-safety `durable_run.py`'s Postgres leases actually give).
  `workspace_root` is optional and defaults to `None`: omitting it keeps
  today's exact prior behavior (a fresh, non-durable, in-memory-only
  walk), so no existing caller is affected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import asyncpg

from app.execution.goal_resolution import ResolvedGoalNode, resolve_goal_via_procedure
from app.execution.goal_verification import run_goal_verification
from app.execution.graph_executor import NodeResult
from app.execution.step_binding import execute_node
from app.models.plan import PlanNode
from app.services.access import AccessScope


@dataclass
class StepAttempt:
    """One real attempt at one bound procedure step (Sec 10: "Track all attempts").

    `verification_state`/`verification_detail` (Sec 9) are the outcome of
    `goal_verification.run_goal_verification` against THIS attempt's result -- `None` only when the step never
    reached `status='success'`. A `status='failure'` attempt whose `verification_state == 'failed_verification'`
    means the step reported success but the Goal's verification requirement caught it."""

    step_order: Optional[int]
    binding_kind: Optional[str]
    status: str  # "success" | "failure"
    notes: Optional[str] = None
    verification_state: Optional[str] = None
    verification_detail: Optional[str] = None


@dataclass
class GoalNodeExecutionResult:
    """The outcome for one `chosen == "step"` leaf. `status` is `"success"` only if the step actually
    succeeded (and passed verification) -- never inferred from a partial result."""

    goal_id: str
    goal_name: str
    status: str  # "success" | "failure"
    attempts: list[StepAttempt] = field(default_factory=list)
    used_binding_kind: Optional[str] = None
    result: Optional[NodeResult] = None
    # True when this result was NOT actually re-executed -- it was
    # reused verbatim from a prior real `goal_node_result` journal event
    # for the same `execution_id` (Sec 12 resume). `attempts` stays
    # empty in that case -- the real attempts happened in a PRIOR
    # process, this run never made them.
    resumed_from_journal: bool = False
    # Real output files this node's winning attempt produced, written to
    # `.stealth/artifacts/` (Sec 5) -- `[{filename, path, sha256,
    # size_bytes}, ...]`, empty for the common case of an Implementation
    # that produces no file output at all. Only populated when durable
    # execution (`workspace_root`) is in effect.
    artifacts: list[dict] = field(default_factory=list)


@dataclass
class ProcedureAttempt:
    """One real attempt at one real Procedure decomposition for one Goal
    node -- the Procedure-level counterpart of `StepAttempt`
    (Sec 10: "Track all attempts"). `status` is the AGGREGATE outcome of
    that procedure's own children walk (`"success"`/`"failure"`/
    `"needs_input"`)."""

    procedure_id: str
    procedure_name: Optional[str]
    status: str


@dataclass
class ProcedureExecutionResult:
    """The outcome for one `chosen == "procedure"` node, including real
    fallback to an alternate decomposition (Prompt 2 Sec 10:
    "alternative Procedure"). `human_intervention_needed` is `True` only
    when EVERY real Procedure this Goal links (the chosen one plus every
    alternate) was actually tried and every one failed -- Sec 10's own
    terminal escalation rung, surfaced honestly rather than silently
    left as an ordinary failure indistinguishable from "only one route
    existed and it failed"."""

    goal_id: str
    goal_name: str
    status: str  # "success" | "failure" | "needs_input"
    attempts: list[ProcedureAttempt] = field(default_factory=list)
    used_procedure_id: Optional[str] = None
    human_intervention_needed: bool = False
    resumed_from_journal: bool = False


@dataclass
class GoalExecutionResult:
    """The full walk's outcome. `outcome` is `"success"` only if every
    reachable `chosen == "step"` leaf succeeded (an
    `"unresolved"` leaf always makes the overall outcome
    `"needs_input"`, Sec 21's own vocabulary -- never silently treated
    as a pass). `node_results` is keyed by `goal_id` (a Goal may appear
    once here even if visited via multiple Procedure branches in the
    source tree -- last real attempt wins, nothing is lost since every
    attempt is already inside that node's own `attempts` list from ITS
    own walk). `procedure_results` is the same idea for every
    `chosen == "procedure"` node actually walked, keyed by `goal_id`."""

    outcome: str  # "success" | "failure" | "needs_input"
    node_results: dict[str, GoalNodeExecutionResult] = field(default_factory=dict)
    procedure_results: dict[str, ProcedureExecutionResult] = field(default_factory=dict)
    unresolved_goal_names: list[str] = field(default_factory=list)
    # Set only when `execute_goal_tree` was called with a real
    # `workspace_root` (Sec 12 durability, opt-in) -- the real
    # `execution_id` this run's `.stealth/events.jsonl` events were
    # recorded under. Pass it back into a later call (same
    # `workspace_root`) to resume THIS SAME attempt rather than start a
    # fresh, unrelated one.
    execution_id: Optional[str] = None


def _to_plan_node(goal_name: str, step: dict) -> PlanNode:
    return PlanNode(order=int(step.get("order") or 0), goal=goal_name, binding=step.get("binding"))


def _journal_prior_result(workspace_root: str, execution_id: str, goal_id: str) -> Optional[dict]:
    """The most recent real `goal_node_result` event this SAME
    `execution_id` already recorded for `goal_id`, or `None` if it was
    never attempted before -- read straight off the real, durable,
    fsync'd local journal (`app.stealth.journal`, the same mechanism
    `generator.py` already uses for Procedure-run projections), never
    reconstructed from in-memory state that a crash would have lost."""
    from app.stealth.journal import read_events
    matches = [
        e for e in read_events(workspace_root)
        if e.get("type") == "goal_node_result" and e.get("execution_id") == execution_id and e.get("goal_id") == goal_id
    ]
    return matches[-1] if matches else None


def _journal_record_result(workspace_root: str, execution_id: str, goal_id: str, **payload) -> None:
    from app.stealth.journal import append_event
    append_event(workspace_root, "goal_node_result", execution_id=execution_id, goal_id=goal_id, **payload)


async def execute_goal_node(
    pool: asyncpg.Pool, node: ResolvedGoalNode, context: dict, *, scope: AccessScope,
) -> GoalNodeExecutionResult:
    """Execute one `chosen == "step"` leaf through its binding, gated by real verification (Sec 9/10):
    if the step reports `status='success'`, run `node.verification_requirement` against the real result via
    `run_goal_verification`. It only counts as a success if execution succeeded AND verification did not come
    back `failed_verification` (`unverified`/`checked`/`verified`/`needs_human_review` all count). Fallback is
    at Procedure level (an alternate Procedure), never inside a step."""
    if node.chosen != "step" or node.step is None:
        raise ValueError(f"execute_goal_node requires a chosen=='step' node, got {node.chosen!r}")

    step = node.step
    plan_node = _to_plan_node(node.goal_name, step)
    result = await execute_node(
        pool, plan_node, {**context, "procedure_id": step.get("procedure_id")}, scope=scope,
    )

    verification_state: Optional[str] = None
    verification_detail: Optional[str] = None
    overall_status = result.status
    if result.status == "success":
        verification = await run_goal_verification(node.verification_requirement, result)
        verification_state, verification_detail = verification.state, verification.detail
        if verification.state == "failed_verification":
            overall_status = "failure"

    kind = (step.get("binding") or {}).get("kind")
    attempt = StepAttempt(
        step_order=step.get("order"), binding_kind=kind, status=overall_status, notes=result.notes,
        verification_state=verification_state, verification_detail=verification_detail,
    )
    return GoalNodeExecutionResult(
        goal_id=node.goal_id, goal_name=node.goal_name, status=overall_status, attempts=[attempt],
        used_binding_kind=kind if overall_status == "success" else None, result=result,
    )


def _aggregate_statuses(statuses: list[str]) -> str:
    """Same priority every layer of this walk uses: an unresolved branch
    always wins (Sec 4's own honest-gap outcome), then a real failure,
    then success -- one real rule, applied uniformly at every depth so a
    procedure's own aggregate status means the same thing whether it is
    the tree root or three levels deep."""
    if any(s == "needs_input" for s in statuses):
        return "needs_input"
    if any(s == "failure" for s in statuses):
        return "failure"
    return "success"


async def _walk_children(
    pool: asyncpg.Pool, children: list[ResolvedGoalNode], context: dict, *, scope: AccessScope,
    leaf_results: dict[str, GoalNodeExecutionResult],
    procedure_results: dict[str, ProcedureExecutionResult],
    unresolved_names: list[str],
    workspace_root: Optional[str] = None, execution_id: Optional[str] = None,
) -> str:
    statuses = [
        await _walk_node(
            pool, child, context, scope=scope, leaf_results=leaf_results,
            procedure_results=procedure_results, unresolved_names=unresolved_names,
            workspace_root=workspace_root, execution_id=execution_id,
        )
        for child in children
    ]
    return _aggregate_statuses(statuses) if statuses else "success"


async def _walk_procedure_with_fallback(
    pool: asyncpg.Pool, node: ResolvedGoalNode, context: dict, *, scope: AccessScope,
    leaf_results: dict[str, GoalNodeExecutionResult],
    procedure_results: dict[str, ProcedureExecutionResult],
    unresolved_names: list[str],
    workspace_root: Optional[str] = None, execution_id: Optional[str] = None,
) -> str:
    """Prompt 2 Sec 10's "alternative Procedure" rung: walk `node`'s own
    children; if the result is a real FAILURE (not `needs_input` -- an
    unresolved step is a structural gap a different decomposition is not
    reliably any better at closing, so this ladder does not spend a real
    fallback attempt on it), lazily resolve and try the next of
    `node.procedure_alternates` in order (`resolve_goal_via_procedure`),
    stopping at the first non-failure outcome. Every attempt's own real
    sub-results are merged into `leaf_results`/`procedure_results`
    regardless of outcome (Sec 10: "Track all attempts") -- a failed
    attempt's real work is not thrown away, it is simply not the one that
    "counts". `human_intervention_needed` is set only when every real
    Procedure this Goal links was actually tried and every one failed --
    Sec 10's own terminal escalation rung, past which this executor has
    no further automatic recourse.

    A resumed (`resumed_from_journal=True`) procedure node (Sec 12) never
    reaches this function at all -- `_walk_node` short-circuits before
    calling it, since a resumed procedure has no real children to walk."""
    procedures_to_try = [(node.procedure, node.children)] + [(alt, None) for alt in node.procedure_alternates]
    attempts: list[ProcedureAttempt] = []
    final_status = "failure"
    winning_procedure_id: Optional[str] = None
    final_unresolved: list[str] = []

    for proc, children in procedures_to_try:
        if children is None:
            alt_tree = await resolve_goal_via_procedure(
                pool, node.goal_id, proc, context=context, scope=scope, depth=node.depth,
            )
            children = alt_tree.children

        attempt_leaf_results: dict[str, GoalNodeExecutionResult] = {}
        attempt_procedure_results: dict[str, ProcedureExecutionResult] = {}
        attempt_unresolved: list[str] = []
        status = await _walk_children(
            pool, children, context, scope=scope, leaf_results=attempt_leaf_results,
            procedure_results=attempt_procedure_results, unresolved_names=attempt_unresolved,
            workspace_root=workspace_root, execution_id=execution_id,
        )
        attempts.append(ProcedureAttempt(procedure_id=str(proc.get("id")), procedure_name=proc.get("name"), status=status))
        leaf_results.update(attempt_leaf_results)
        procedure_results.update(attempt_procedure_results)
        final_status, final_unresolved = status, attempt_unresolved
        if status != "failure":
            winning_procedure_id = str(proc.get("id"))
            break

    unresolved_names.extend(final_unresolved)
    human_intervention_needed = final_status == "failure" and all(a.status == "failure" for a in attempts)
    if workspace_root and execution_id:
        _journal_record_result(
            workspace_root, execution_id, node.goal_id, kind="procedure", status=final_status,
            used_procedure_id=winning_procedure_id, human_intervention_needed=human_intervention_needed,
        )
    procedure_results[node.goal_id] = ProcedureExecutionResult(
        goal_id=node.goal_id, goal_name=node.goal_name, status=final_status,
        attempts=attempts, used_procedure_id=winning_procedure_id,
        human_intervention_needed=human_intervention_needed,
    )
    return final_status


async def _walk_node(
    pool: asyncpg.Pool, node: ResolvedGoalNode, context: dict, *, scope: AccessScope,
    leaf_results: dict[str, GoalNodeExecutionResult],
    procedure_results: dict[str, ProcedureExecutionResult],
    unresolved_names: list[str],
    workspace_root: Optional[str] = None, execution_id: Optional[str] = None,
) -> str:
    if node.chosen == "step":
        if workspace_root and execution_id:
            prior = _journal_prior_result(workspace_root, execution_id, node.goal_id)
            if prior and prior.get("status") == "success":
                result = GoalNodeExecutionResult(
                    goal_id=node.goal_id, goal_name=node.goal_name, status="success",
                    used_binding_kind=prior.get("used_binding_kind"), resumed_from_journal=True,
                    artifacts=prior.get("artifacts") or [],
                )
                leaf_results[node.goal_id] = result
                return "success"
        result = await execute_goal_node(pool, node, context, scope=scope)
        if workspace_root and execution_id and result.result and result.result.data:
            output_files = result.result.data.get("output_files")
            if output_files:
                from app.stealth.artifacts import write_execution_artifacts
                result.artifacts = write_execution_artifacts(workspace_root, node.goal_id, execution_id, output_files)
        leaf_results[node.goal_id] = result
        if workspace_root and execution_id:
            _journal_record_result(
                workspace_root, execution_id, node.goal_id, kind="step",
                status=result.status, used_binding_kind=result.used_binding_kind,
                artifacts=result.artifacts,
            )
        return result.status
    if node.chosen == "unresolved":
        unresolved_names.append(node.goal_name)
        return "needs_input"
    if workspace_root and execution_id:
        prior = _journal_prior_result(workspace_root, execution_id, node.goal_id)
        if prior and prior.get("status") == "success":
            result = ProcedureExecutionResult(
                goal_id=node.goal_id, goal_name=node.goal_name, status="success",
                used_procedure_id=prior.get("used_procedure_id"), resumed_from_journal=True,
            )
            procedure_results[node.goal_id] = result
            return "success"
    return await _walk_procedure_with_fallback(
        pool, node, context, scope=scope, leaf_results=leaf_results,
        procedure_results=procedure_results, unresolved_names=unresolved_names,
        workspace_root=workspace_root, execution_id=execution_id,
    )


async def execute_goal_tree(
    pool: asyncpg.Pool, tree: ResolvedGoalNode, context: dict, *, scope: AccessScope,
    workspace_root: Optional[str] = None, execution_id: Optional[str] = None,
) -> GoalExecutionResult:
    """Walk an already-resolved Goal tree (`goal_resolution.resolve_goal`'s
    own output) and execute every real `step` leaf it
    contains, in the same order the tree's own Procedure steps were
    already sorted in -- with real fallback when a whole
    Procedure's execution fails: an alternate Procedure
    (`_walk_procedure_with_fallback`, lazily resolving
    `node.procedure_alternates` only if actually needed). An `unresolved`
    leaf is never executed and never silently treated as success -- it
    stops that branch honestly (Sec 4: "A Goal may initially be unsolved"
    is a real outcome, not an error to paper over) and the overall
    `outcome` becomes `"needs_input"`.

    `workspace_root` (Sec 12, opt-in): when given, every node's real
    outcome is durably recorded via `app.stealth.journal`, and a node
    whose last recorded outcome under the SAME `execution_id` was already
    `'success'` is reused rather than re-executed -- real resume after a
    real crash, using the same local journal `generator.py` already
    relies on. `execution_id` defaults to a fresh `uuid7` (returned on
    the result) when `workspace_root` is given but no `execution_id` is;
    pass the SAME pair back in on a later call to resume that exact
    attempt."""
    leaf_results: dict[str, GoalNodeExecutionResult] = {}
    procedure_results: dict[str, ProcedureExecutionResult] = {}
    unresolved_names: list[str] = []

    if workspace_root and not execution_id:
        from app.utils.ids import uuid7_str
        execution_id = uuid7_str()

    outcome = await _walk_node(
        pool, tree, context, scope=scope, leaf_results=leaf_results,
        procedure_results=procedure_results, unresolved_names=unresolved_names,
        workspace_root=workspace_root, execution_id=execution_id,
    )

    result = GoalExecutionResult(
        outcome=outcome, node_results=leaf_results, procedure_results=procedure_results,
        unresolved_goal_names=unresolved_names, execution_id=execution_id if workspace_root else None,
    )
    if workspace_root:
        _write_goal_run_md(workspace_root, result)
    return result


def render_goal_run_md(execution: GoalExecutionResult) -> str:
    """Prompt 2 Sec 13: the human/agent-readable Goal-execution trace,
    built directly from `execute_goal_tree`'s own already-computed
    result -- never re-derived from the journal or re-executed. Real,
    honest, `rg`-able per `GoalNode|<goal_id>|...` (see `pipe_format.py`'s
    own module comment on why this is a SEPARATE page from `run.md`, not
    a shoehorned extension of its Procedure-anchored grammar)."""
    from app.stealth.pipe_format import GoalRunLine, render_goal_run_md as _render

    lines = [
        GoalRunLine(
            goal_id=gid, kind="step", status=r.status, goal_name=r.goal_name,
            binding=r.used_binding_kind, verification_state=(
                r.attempts[-1].verification_state if r.attempts else None
            ),
            resumed_from_journal=r.resumed_from_journal, artifacts=r.artifacts,
        )
        for gid, r in execution.node_results.items()
    ] + [
        GoalRunLine(
            goal_id=gid, kind="procedure", status=r.status, goal_name=r.goal_name,
            procedure_id=r.used_procedure_id, human_intervention_needed=r.human_intervention_needed,
            resumed_from_journal=r.resumed_from_journal,
        )
        for gid, r in execution.procedure_results.items()
    ]
    return _render(execution.execution_id or "-", execution.outcome, lines)


def write_goal_run_md_file(workspace_root: str, content: str) -> None:
    """Writes `.stealth/goal_run.md` for real (Sec 13: "Keep run
    artifacts durable and inspectable") -- same atomic-write + single-
    writer-lock discipline `generator.py` already uses for every other
    `.stealth/` page, reused here rather than a second write mechanism.
    Real, standalone, and content-agnostic -- any caller with a real
    already-rendered `goal_run.md` body (execute-time, via `render_
    goal_run_md` above, or compile-time, via `goal_compiler.py::
    compiled_goal_to_run_md`) writes through this ONE function, so both
    ever produce the file the identical, real way."""
    import os

    from app.stealth.atomic import atomic_write_batch
    from app.stealth.journal import STEALTH_DIRNAME, SingleWriterLock

    path = os.path.join(workspace_root, STEALTH_DIRNAME, "goal_run.md")
    with SingleWriterLock(workspace_root):
        atomic_write_batch([(path, content)])


def _write_goal_run_md(workspace_root: str, execution: GoalExecutionResult) -> None:
    write_goal_run_md_file(workspace_root, render_goal_run_md(execution))


def read_goal_run_status(workspace_root: str) -> Optional[dict]:
    """The real read side of `write_goal_run_md_file`/`compile_goal`'s
    `workspace_root` write -- Sec 14's "run status" MCP-tool gap this
    closes. Reads `.stealth/goal_run.md` (compile-time `"planned"` or
    execute-time real outcome, same grammar -- `pipe_format.py`'s own
    module header) and returns `parse_goal_run_md`'s structured result.

    Returns `None` when no `goal_run.md` exists at this `workspace_root`
    -- a real, common state (never compiled/executed yet here), not an
    error to raise. No lock needed: a plain read of an atomically-
    written file is always either the old or the new complete content,
    never a partial one (`atomic_write_batch`'s own guarantee)."""
    import os

    from app.stealth.journal import STEALTH_DIRNAME
    from app.stealth.pipe_format import parse_goal_run_md

    path = os.path.join(workspace_root, STEALTH_DIRNAME, "goal_run.md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return parse_goal_run_md(f.read())
