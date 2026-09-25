"""
Recursive Goal -> Procedure compiler (execu.md Sec 0/2/8).

    Goal
        |
    choose feasible Procedure
        |
    each step is either a concrete executable STEP (it carries a `binding`) or a subgoal
        |
    recursively resolve subgoals

There is no Implementation object: "an implementation" is one step (or a whole one-step procedure) whose
step carries a `binding` and a `source_locator`.

Deliberately deterministic -- no LLM call anywhere in this file. It starts FROM an already-real `goal_id`.
  - looks up Procedures that `achieves_goal_id` this Goal and checks each one's real feasibility via
    applicability.check_hard_constraints (the same mechanism find_best_way uses),
  - recurses into each chosen Procedure's steps: bound steps become executable leaves, others resolve as goals.

Reuses goals.py::normalize_goal_name (the same dedup key find_or_create_goal uses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import asyncpg

from app.services.access import AccessScope
from app.services.applicability import _CANDIDATE_BASE_WHERE, PROCEDURE_COLS_NO_HEAVY, check_hard_constraints
from app.services.goal_ranking import ProcedureRankingService
from app.services.goals import normalize_goal_name
from app.services.routed_reads import fetch_goal

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
    chosen: Literal["step", "procedure", "unresolved"]
    # chosen == "step": one concrete, executable procedure step (it carries a `binding`). A one-step procedure
    # is just a procedure node with a single step child; there is no separate Implementation object.
    step: Optional[dict] = None
    # This Goal's own real `goals.verification_requirement` (migration
    # 83, JSONB, default '{}'). Threaded through unchanged as the Goal's
    # success check for the planner, never invented -- an empty
    # dict is the honest, common default, not an error.
    verification_requirement: dict = field(default_factory=dict)
    procedure: Optional[dict] = None
    # Real other feasible Procedures linked to this same Goal, beyond the
    # one chosen (in the order established by the central ranking, or the
    # original order when that read is unavailable) -- kept, not
    # discarded, so the planner agent can fall back to an alternate
    # decomposition strategy when the chosen one fails (Prompt 2 Sec 10:
    # "alternative Procedure"), the same "keep the real runner-ups" discipline. Each entry is the real procedure ROW (id,
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
    from app.services.routed_reads import find_goals_by_exact_names

    normalized = normalize_goal_name(text or "")
    if not normalized:
        return None
    # The global goal_names index covers Goals homed on every shard (a query on
    # this database's own `goals` table would miss remote ones); the caller's
    # scope is preferred over 'global', exactly as before.
    found = await find_goals_by_exact_names(
        pool, [normalized], scope_type=scope_type, scope_entity_id=scope_entity_id,
    )
    return found.get(normalized)


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
    from app.services.routed_reads import fetch_goal_procedures

    # A Goal's Procedures usually share its shard but are not guaranteed to
    # (rollover): locate them through the projection and read only the shards
    # that hold them, instead of asking every shard.
    rows = await fetch_goal_procedures(
        pool, [goal_id], columns=PROCEDURE_COLS_NO_HEAVY, where=_CANDIDATE_BASE_WHERE,
    )
    rows = sorted(rows, key=lambda r: (r["verification_state"] != "verified", -r["t_created"].timestamp()))[:20]
    results: list[tuple[dict, bool]] = []
    for row in rows:
        proc = dict(row)
        result = await check_hard_constraints(
            pool, proc, current_scope=current_scope, access_scope=access_scope, require_verified=False,
        )
        results.append((proc, result.applicable))
    return results


async def _rank_feasible_procedures(
    pool: asyncpg.Pool,
    feasible: list[dict],
    *,
    current_scope: dict,
    access_scope: AccessScope,
) -> list[dict]:
    if len(feasible) < 2:
        return feasible
    try:
        service = ProcedureRankingService(
            pool,
            scope=access_scope,
            current_scope=current_scope,
            require_verified=False,
        )
        procedure_ids = [str(proc["id"]) for proc in feasible]
        ranker = getattr(service, "rank_for_goal", None)
        if not callable(ranker):
            ranker = service.rank
        ranked = await ranker(procedure_ids)
    except Exception:
        return feasible
    if not isinstance(ranked, (list, tuple)) or not ranked:
        return feasible
    ranks: dict[str, int] = {}
    for position, result in enumerate(ranked):
        if not isinstance(result, dict):
            return feasible
        row_id = result.get("procedure_row_id") or result.get("id")
        if row_id is None:
            continue
        raw_rank = result.get("rank")
        try:
            rank = int(raw_rank) if raw_rank is not None else position + 1
        except (TypeError, ValueError):
            rank = position + 1
        ranks.setdefault(str(row_id), rank)
    if not ranks:
        return feasible
    fallback_rank = max(ranks.values()) + 1
    return sorted(
        feasible,
        key=lambda proc: ranks.get(str(proc["id"]), fallback_rank),
    )


def _procedure_cost_score(proc: dict) -> Optional[float]:
    """Real, named extension point for Prompt 2 Sec 11's Procedure-level
    cost routing -- NOT implemented yet, by explicit founder direction
    ("rank it by cost eventually, but for now don't estimate -- create a
    placeholder we can plug a real scoring function into, don't add
    anything for now"). Always returns `None` today, so this hook does not
    affect the central ranking or selection -- it is called but its result
    does not yet participate in routing.

    A real version later will need to eagerly resolve+cost EVERY feasible
    candidate's own subtree -- a real cost/complexity tradeoff, deliberately
    deferred rather than attempted here."""
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
        if isinstance(step, dict) and step.get("binding"):
            children.append(ResolvedGoalNode(
                goal_id=f"{goal['id']}#s{step.get('order')}", goal_name=str(step.get("goal") or step.get("description") or step.get("action") or "(unnamed step)"),
                depth=depth + 1, chosen="step", step={**step, "procedure_id": str(proc.get("procedure_id") or proc.get("id"))},
                verification_requirement=step.get("verifier") or (step["binding"].get("verifier") or {}),
                rationale=f"step {step.get('order')} carries a binding ({step['binding'].get('kind')})",
            ))
            continue
        step_goal_row = None
        step_goal_id = step.get("goal_id")  # future-proofing: a real per-step FK, once one exists (none does today)
        if step_goal_id:
            step_goal_row = await fetch_goal(pool, str(step_goal_id), columns="id")
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
    -- Prompt 2 Sec 10's "alternative Procedure" fallback rung, for use
    ONLY if/when the first-choice Procedure actually fails (resolving every alternate eagerly inside
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
    goal_row = await fetch_goal(pool, goal_id)
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
            retrieve Procedures
            evaluate feasible Procedure paths
            instantiate the winner: bound steps -> executable leaves, other steps -> child Goals

    Deterministic end to end. `embedder` is accepted for backward compatibility and unused.

    `context["current_scope"]`: the caller-supplied dict describing real current task context, passed to
    `check_hard_constraints` unchanged.

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

    goal_row = await fetch_goal(pool, goal_id)
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

    # Procedure decomposition path
    current_scope = context.get("current_scope") or {}
    candidates = await _feasible_procedures_for_goal(pool, goal_id, current_scope=current_scope, access_scope=scope)
    feasible = [p for p, ok in candidates if ok]
    feasible = await _rank_feasible_procedures(
        pool, feasible, current_scope=current_scope, access_scope=scope,
    )
    # Sec 11 cost-routing hook (real, inert -- see _procedure_cost_score's
    # own docstring): called so it is exercised/discoverable, but its
    # result does not affect the central ranking or selection today.
    for _proc in feasible:
        _proc["_cost_score"] = _procedure_cost_score(_proc)
    # Repo facts (find_ways, final_thing.md): an optional request-scoped
    # selector re-orders feasible Procedures by the claim-conditioned judge
    # and drops ones whose REQUIRED conditions the repo contradicts. Absent
    # (every other caller), selection is exactly as before.
    repo_rejected: list[dict] = []
    selector = context.get("_procedure_selector")
    if selector is not None and feasible:
        feasible, repo_rejected = await selector(goal_name, feasible, depth=depth)
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
                # The full step list travels with the choice: the planner agent
                # compiles the plan from it (final_architecture.md), so a step
                # with no binding and no matching sub-Goal is still an
                # instruction it can carry out, not a dead end.
                "goal": proc.get("goal"), "description": proc.get("display_description"),
                "verification_state": proc.get("verification_state"),
                "preconditions": proc.get("preconditions") or [],
                "steps": sorted(proc.get("steps") or [], key=lambda s: s.get("order", 0) if isinstance(s, dict) else 0),
                **({"repo_fit": proc["_repo_fit"]} if proc.get("_repo_fit") else {}),
            },
            verification_requirement=goal.get("verification_requirement") or {},
            children=children, procedure_alternates=feasible[1:],
            rationale=(
                f"selected procedure {proc.get('name')!r} ({proc['id']}) among "
                f"{len(feasible)} feasible / {len(candidates)} linked to this Goal"
            ),
            procedures_linked=len(candidates), procedures_feasible=len(feasible),
        )

    # nothing feasible -- honest, explained unresolved
    reasons = []
    if repo_rejected:
        reasons.append(
            f"{len(repo_rejected)} feasible procedure(s) contradicted by repo facts: "
            + ", ".join(
                f"{p.get('name')!r} (facts {','.join((p.get('_repo_fit') or {}).get('blocking_fact_ids') or []) or '?'})"
                for p in repo_rejected
            )
        )
    elif candidates:
        reasons.append(f"{len(candidates)} procedure(s) linked, none feasible")
    if not reasons:
        reasons.append("no procedure linked to this goal")
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=goal_name, depth=depth, chosen="unresolved",
        unresolved_reason="; ".join(reasons),
        procedures_linked=len(candidates), procedures_feasible=0,
    )
