"""
Recursive Goal -> Implementation/Procedure compiler (execu.md Sec 0/2/8,
2026-09-15) -- the actual core deliverable of the runtime directive:

    Goal
        |
    choose direct Implementation
    OR
    choose Procedure
        |
    subgoals
        |
    recursively resolve
        |
    concrete execution DAG (a tree here; DAG flattening is compile_goal's
    own job in the MCP layer, not this module's)

Deliberately deterministic (Sec 10's own split: "graph construction:
deterministic rules"). No LLM call anywhere in this file -- semantic
Goal/Procedure grounding is a separate, EARLIER stage
(step_grounding.py); this module starts FROM an already-real `goal_id`,
it never interprets free text into meaning itself. It only:
  - looks up direct Implementations for a Goal (structured lookup,
    implementation_selection.py's real ranker, already built/tested),
  - looks up Procedures that `achieves_goal_id` this Goal (structured
    lookup) and checks each one's real feasibility via
    applicability.py's own non-compensatory hard-constraint cascade --
    the SAME mechanism find_best_way already uses, not a second
    applicability system,
  - recurses into each chosen Procedure's own steps' child Goals.

Reuses, does not reinvent:
  - implementation_selection.select_implementation_for_goal_id (the
    real FK-based lookup + filter + rank, Sec 8-10, already tested)
  - applicability.check_hard_constraints (the real non-compensatory
    cascade, already tested)
  - goals.py::normalize_goal_name (the SAME dedup key
    find_or_create_goal uses, so a step-text match here is the
    identical key a write would have deduped against)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import asyncpg

from app.execution.implementation_selection import select_implementation_for_goal_id
from app.services.access import AccessScope
from app.services.applicability import _CANDIDATE_BASE_WHERE, PROCEDURE_COLS_NO_HEAVY, check_hard_constraints
from app.services.goals import normalize_goal_name

DEFAULT_MAX_DEPTH = 6


class GoalResolutionError(Exception):
    """A real, caller-facing failure (e.g. the ROOT goal_id does not
    resolve at all) -- distinct from an "unresolved" leaf deep in the
    tree, which is a normal, valid, non-error outcome (Sec 4: "A Goal
    may initially be unsolved")."""


@dataclass
class ResolvedGoalNode:
    """One node of the resolved Goal tree. `chosen='unresolved'` is a
    real, honest terminal state, never papered over -- `unresolved_reason`
    always says why. `goal_id='-'` (not a real id) only for the rare
    case where a step's own goal text matched no canonical Goal at all
    -- everything else carries a real `goals.id`."""

    goal_id: str
    goal_name: str
    depth: int
    chosen: Literal["implementation", "procedure", "unresolved"]
    implementation: Optional[dict] = None
    # Real eligible runner-up Implementations for this Goal, already
    # ranked by `select_implementation_for_goal_id`'s own scorer
    # (best-to-worst, `implementation` excluded) -- kept so a real
    # executor (goal_execution.py) can fall back to the next real
    # candidate on failure (Prompt 2 Sec 10) without a second query.
    # Never fabricated: exactly `SelectionResult.ranked`'s own eligible
    # entries, same data `explain_goal_route` already discloses via
    # `implementation_candidates_considered`'s count, just not thrown away.
    implementation_alternates: list[dict] = field(default_factory=list)
    # This Goal's own real `goals.verification_requirement` (migration
    # 83, JSONB, default '{}' -- a real column, unpopulated by anything
    # until Prompt 2 Sec 9's goal_verification.py gave it a real
    # consumer). Threaded through unchanged, never invented -- an empty
    # dict is the honest, common default, not an error.
    verification_requirement: dict = field(default_factory=dict)
    procedure: Optional[dict] = None
    # Real other feasible Procedures linked to this same Goal, beyond the
    # one chosen (already-ordered verified-first/recency-second, same
    # order `_feasible_procedures_for_goal` returned) -- kept, not
    # discarded, so a real executor (goal_execution.py) can fall back to
    # an alternate decomposition strategy when the chosen one's own
    # execution fails (Prompt 2 Sec 10: "alternative Procedure"), the
    # same "keep the real runner-ups" discipline `implementation_alternates`
    # already established. Each entry is the real procedure ROW (id,
    # procedure_id, name, version, steps, ...), not a pre-resolved tree --
    # resolving every alternate's own subgoals eagerly would be real,
    # wasted recursive work for the overwhelmingly common case where the
    # first procedure succeeds; `resolve_goal_via_procedure` below
    # resolves one lazily, only if/when it is actually needed.
    procedure_alternates: list[dict] = field(default_factory=list)
    children: list["ResolvedGoalNode"] = field(default_factory=list)
    rationale: str = ""
    unresolved_reason: Optional[str] = None
    # Every candidate considered at this node, kept for real
    # explainability (Sec 15's "route selection is explainable" -- an
    # MCP explain_goal_route caller reads this, never recomputes it).
    implementation_candidates_considered: int = 0
    procedures_linked: int = 0
    procedures_feasible: int = 0


async def resolve_goal_id_for_text(
    pool: asyncpg.Pool, text: str, *, scope_type: Optional[str], scope_entity_id: Optional[str],
) -> Optional[dict]:
    """The real, honest text->Goal lookup: exact normalized-name match
    against the real `goals` table (backend/db/83_goals.sql), using the
    SAME normalization `find_or_create_goal` uses for its own dedup key
    -- a match here is the identical key a write would have deduped
    against, never a looser or stricter rule invented here. Scoped the
    same way writes are scoped (global, or this caller's own
    scope_type+scope_entity_id, preferring the more specific match).
    Returns `None` (never fabricated) when nothing matches -- the
    overwhelmingly common case for a step whose own text was never
    itself ingested as a Goal name.
    """
    normalized = normalize_goal_name(text or "")
    if not normalized:
        return None
    if scope_type and scope_type != "global":
        rows = await pool.fetch(
            "SELECT * FROM goals WHERE normalized_name = $1 AND t_invalid IS NULL "
            "AND ((scope_type = $2 AND scope_entity_id IS NOT DISTINCT FROM $3) OR scope_type = 'global') "
            "ORDER BY (scope_type != 'global') ASC LIMIT 1",
            normalized, scope_type, scope_entity_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM goals WHERE normalized_name = $1 AND t_invalid IS NULL AND scope_type = 'global' LIMIT 1",
            normalized,
        )
    return dict(rows[0]) if rows else None


async def _feasible_procedures_for_goal(
    pool: asyncpg.Pool, goal_id: str, *, current_scope: dict, access_scope: AccessScope,
) -> list[tuple[dict, bool]]:
    """Real Procedures whose `achieves_goal_id` names this Goal
    (backend/db/83_goals.sql's own FK, `ingestion` lane), each real-
    feasibility-checked via `applicability.py`'s own non-compensatory
    hard-constraint cascade -- never a second applicability mechanism.

    `require_verified=False`: a Goal->Procedure link here is a real,
    structural fact (the `achieves_goal_id` FK itself), not automatic
    candidate SEARCH the cold-start gate exists to guard (ticket 13's
    own "explicit invocation" posture -- this compiler already knows
    exactly which Procedures claim to achieve this Goal; it is not
    fishing through the whole corpus by similarity).
    """
    rows = await pool.fetch(
        f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures WHERE achieves_goal_id = $1::uuid AND {_CANDIDATE_BASE_WHERE} "
        "ORDER BY (verification_state = 'verified') DESC, t_created DESC LIMIT 20",
        goal_id,
    )
    results: list[tuple[dict, bool]] = []
    for row in rows:
        proc = dict(row)
        result = await check_hard_constraints(
            pool, proc, current_scope=current_scope, access_scope=access_scope, require_verified=False,
        )
        results.append((proc, result.applicable))
    return results


def _procedure_cost_score(proc: dict) -> Optional[float]:
    """Real, named extension point for Prompt 2 Sec 11's Procedure-level
    cost routing -- NOT implemented yet, by explicit founder direction
    ("rank it by cost eventually, but for now don't estimate -- create a
    placeholder we can plug a real scoring function into, don't add
    anything for now"). Always returns `None` today, so `resolve_goal()`'s
    existing verified-first/recency-second ordering (`feasible[0]`) is
    completely unchanged -- this function is called but its result does
    not yet affect selection.

    A real implementation later will need to eagerly resolve+cost EVERY
    feasible candidate's own subtree (via `_resolve_procedure_children` +
    `goal_cost.estimate_goal_cost`), unlike Implementation-level cost
    ranking (`implementation_selection.py`, already real and wired in),
    which only needs one batched telemetry query per candidate -- a real
    cost/complexity tradeoff, deliberately deferred rather than
    attempted here."""
    return None


async def _resolve_procedure_children(
    pool: asyncpg.Pool, goal: dict, proc: dict, *, context: dict, scope: AccessScope,
    depth: int, max_depth: int, visited: frozenset, embedder: Optional[Any] = None,
) -> list["ResolvedGoalNode"]:
    """Resolve one Procedure's own steps into child `ResolvedGoalNode`s --
    factored out of `resolve_goal`'s own procedure branch so
    `resolve_goal_via_procedure` (a lazy, real ALTERNATE-procedure
    resolution, Prompt 2 Sec 10) can build a child list for a candidate
    procedure that was NOT the one `resolve_goal` originally chose,
    using the identical step->Goal resolution logic -- one real
    mechanism, not a second one."""
    steps = sorted(proc.get("steps") or [], key=lambda s: s.get("order", 0))
    children: list[ResolvedGoalNode] = []
    for step in steps:
        step_goal_row = None
        step_goal_id = step.get("goal_id")  # future-proofing: a real per-step FK, once one exists (none does today)
        if step_goal_id:
            step_goal_row = await pool.fetchrow(
                "SELECT id FROM goals WHERE id = $1::uuid AND t_invalid IS NULL", step_goal_id,
            )
        if step_goal_row is None:
            step_goal_row = await resolve_goal_id_for_text(
                pool, step.get("goal") or step.get("action") or "",
                scope_type=goal.get("scope_type"), scope_entity_id=goal.get("scope_entity_id"),
            )
        if step_goal_row is None:
            children.append(ResolvedGoalNode(
                goal_id="-", goal_name=step.get("goal") or step.get("action") or "(unnamed step)",
                depth=depth + 1, chosen="unresolved",
                unresolved_reason="step's goal text does not match any canonical Goal",
            ))
            continue
        child = await resolve_goal(
            pool, str(step_goal_row["id"]), context=context, scope=scope,
            depth=depth + 1, max_depth=max_depth, visited=visited, embedder=embedder,
        )
        children.append(child)
    return children


async def resolve_goal_via_procedure(
    pool: asyncpg.Pool, goal_id: str, procedure: dict, *,
    context: Optional[dict] = None, scope: AccessScope, depth: int = 0, max_depth: int = DEFAULT_MAX_DEPTH,
    embedder: Optional[Any] = None,
) -> ResolvedGoalNode:
    """Lazy, real resolution of ONE SPECIFIC alternate Procedure for a
    Goal that already has a resolved tree via its FIRST-choice Procedure
    -- Prompt 2 Sec 10's "alternative Procedure" fallback rung, called
    by `goal_execution.py` ONLY if/when the first-choice Procedure's own
    execution actually fails (resolving every alternate eagerly inside
    `resolve_goal` itself would be real, wasted recursive work for the
    overwhelmingly common case where the first choice succeeds).

    `procedure` is a real row already in `ResolvedGoalNode.procedure_
    alternates` -- this function does not search for one, it resolves
    the ONE given. `visited` is reset to just this `goal_id` (this is a
    fresh lazy resolution rooted here, not a continuation of the
    original tree's own recursion path) -- an honest approximation
    disclosed here rather than silently reusing stale state from a
    resolution that already finished.
    """
    context = context or {}
    goal_row = await pool.fetchrow("SELECT * FROM goals WHERE id = $1::uuid AND t_invalid IS NULL", goal_id)
    if goal_row is None:
        raise GoalResolutionError(f"goal_id {goal_id!r} does not exist or is not live")
    goal = dict(goal_row)
    goal_name = str(goal.get("canonical_name") or goal_id)

    children = await _resolve_procedure_children(
        pool, goal, procedure, context=context, scope=scope,
        depth=depth, max_depth=max_depth, visited=frozenset({goal_id}), embedder=embedder,
    )
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="procedure",
        procedure={
            "id": str(procedure["id"]), "procedure_id": str(procedure["procedure_id"]),
            "name": procedure.get("name"), "version": procedure.get("version"),
        },
        verification_requirement=goal.get("verification_requirement") or {},
        children=children,
        rationale=f"alternate procedure {procedure.get('name')!r} ({procedure['id']}) tried after the first choice failed",
    )


async def resolve_goal(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    context: Optional[dict] = None,
    scope: AccessScope,
    depth: int = 0,
    max_depth: int = DEFAULT_MAX_DEPTH,
    visited: Optional[frozenset] = None,
    embedder: Optional[Any] = None,
) -> ResolvedGoalNode:
    """The real Sec 8 recursive compiler:

        resolve_goal(G, context):
            retrieve direct Implementations
            retrieve Procedures
            evaluate feasible direct paths
            evaluate feasible Procedure paths
            if direct Implementation wins: emit concrete execution node
            if Procedure wins: instantiate, recurse into child Goals

    Deterministic end to end -- no LLM call anywhere in this function.
    `embedder` (opt-in, `None` by default -- every existing caller keeps
    its exact prior behavior) is the one real EXCEPTION to "no LLM call":
    not an LLM completion, but a real embedding API call, threaded into
    `select_implementation_for_goal_id` (Sec 11 follow-up: hybrid
    semantic Goal->Implementation retrieval) so an Implementation whose
    real meaning matches this Goal, but was never linked via an exact
    `goal_id` FK, can still be discovered -- the exact `goal_id` match
    stays the strongest real signal, never replaced.

    `context["current_scope"]`: the caller-supplied dict describing real
    current task context (repo/files/etc), threaded straight into both
    `select_implementation_for_goal_id` and `check_hard_constraints`
    unchanged -- this function invents no context of its own.

    Cycle prevention: `visited` carries every goal_id already being
    resolved higher in THIS recursion path (not the whole tree) -- the
    SAME Goal legitimately appearing in two unrelated branches is fine;
    only a genuine cycle (a Procedure's own subgoal chain leading back to
    a Goal it is already trying to satisfy) is refused, as `unresolved`
    with a real, named reason, never a silent infinite loop.

    Recursion limit: `max_depth` (default `DEFAULT_MAX_DEPTH`) bounds
    runaway decomposition the same honest way -- a Goal beyond the limit
    is `unresolved`, never truncated silently.
    """
    context = context or {}
    visited = visited or frozenset()

    goal_row = await pool.fetchrow("SELECT * FROM goals WHERE id = $1::uuid AND t_invalid IS NULL", goal_id)
    if goal_row is None:
        if depth == 0:
            raise GoalResolutionError(f"root goal_id {goal_id!r} does not exist or is not live")
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name="?", depth=depth, chosen="unresolved",
            unresolved_reason="goal not found or not visible",
        )
    goal = dict(goal_row)
    goal_name = str(goal.get("canonical_name") or goal_id)

    if goal_id in visited:
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="unresolved",
            unresolved_reason="cycle detected -- this Goal is already being resolved higher in this recursion path",
        )
    if depth >= max_depth:
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="unresolved",
            unresolved_reason=f"recursion limit ({max_depth}) reached",
        )

    next_visited = visited | {goal_id}

    # A. direct Implementation path (tried first -- a real, concrete
    # leaf is always preferred over decomposing further when one exists
    # and is feasible; Sec 8's own ordering).
    selection = await select_implementation_for_goal_id(
        pool, goal_id, context=context, scope=scope, goal_text=goal_name, embedder=embedder,
    )
    if selection.chosen is not None:
        eligible_ranked = [r.implementation for r in selection.ranked if r.eligible]
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="implementation",
            implementation=selection.chosen, implementation_alternates=eligible_ranked[1:],
            verification_requirement=goal.get("verification_requirement") or {},
            rationale=selection.rationale,
            implementation_candidates_considered=len(selection.candidates_considered),
        )

    # B. Procedure decomposition path
    current_scope = context.get("current_scope") or {}
    candidates = await _feasible_procedures_for_goal(pool, goal_id, current_scope=current_scope, access_scope=scope)
    feasible = [p for p, ok in candidates if ok]
    # Sec 11 cost-routing hook (real, inert -- see _procedure_cost_score's
    # own docstring): called so it is exercised/discoverable, but its
    # result does not affect selection today. `proc = feasible[0]` below
    # stays exactly the existing verified-first/recency-second pick.
    for _proc in feasible:
        _proc["_cost_score"] = _procedure_cost_score(_proc)
    if feasible:
        proc = feasible[0]  # already ordered verified-first, recency-second by the query itself
        children = await _resolve_procedure_children(
            pool, goal, proc, context=context, scope=scope, depth=depth, max_depth=max_depth,
            visited=next_visited, embedder=embedder,
        )
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="procedure",
            procedure={
                "id": str(proc["id"]), "procedure_id": str(proc["procedure_id"]),
                "name": proc.get("name"), "version": proc.get("version"),
            },
            verification_requirement=goal.get("verification_requirement") or {},
            children=children, procedure_alternates=feasible[1:],
            rationale=(
                f"selected procedure {proc.get('name')!r} ({proc['id']}) among "
                f"{len(feasible)} feasible / {len(candidates)} linked to this Goal"
            ),
            implementation_candidates_considered=len(selection.candidates_considered),
            procedures_linked=len(candidates), procedures_feasible=len(feasible),
        )

    # C. nothing feasible -- honest, explained unresolved
    reasons = []
    if selection.candidates_considered:
        reasons.append(f"{len(selection.candidates_considered)} direct implementation(s) considered, none eligible")
    if candidates:
        reasons.append(f"{len(candidates)} procedure(s) linked, none feasible")
    if not reasons:
        reasons.append("no direct implementation and no procedure linked to this goal")
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="unresolved",
        unresolved_reason="; ".join(reasons),
        implementation_candidates_considered=len(selection.candidates_considered),
        procedures_linked=len(candidates), procedures_feasible=0,
    )
