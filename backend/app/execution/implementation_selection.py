"""
Deterministic Implementation selection for a grounded ProcedureStep goal
(meta-harness directive Sec 8-10, 2026-09-15).

Scope, stated honestly: this module implements the STRUCTURED half of
Implementation selection only -- goal-based grouping (structured lookup,
no LLM), deterministic hard-constraint filtering, and an explainable V1
ranker over survivors. It does NOT do:

  - semantic grounding of an abstract ProcedureStep into this goal string
    (that is the LLM grounding stage the directive describes separately --
    out of scope for this module, which starts FROM an already-grounded
    goal),
  - graph/DAG compilation (app/execution/plans.py::compile_plan and
    app/execution/procedure_graph.py own that, and per the directive "the
    ranker selects Implementations, the compiler owns graph structure" --
    this module is exactly the ranker, nothing more),
  - execution dispatch (app/execution/implementation_executor.py).

The ranker is deliberately NOT learned (directive: "Do NOT build a learned
ranker yet") and deliberately does NOT hardcode a global preference order
like "LSP > rg > Claude" -- every signal is conditional on the caller-
supplied `context`, and every candidate's full rejection/score reasoning is
returned, never discarded, so a caller (or `.stealth/run.md`'s selection
rationale line) can show its work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import asyncpg

from app.execution.cost_math import expected_attempts, expected_value
from app.execution.execution_telemetry import ImplementationExecutionStats, implementation_execution_stats_batch
from app.execution.implementation_registry import list_implementations_by_goal
from app.services.access import AccessScope
from app.services.procedure_extraction.capability import compute_capability, CapabilityScope, OutcomeRecord

RequirementState = Literal["HARD_FALSE", "SOFT", "SATISFIABLE", "UNKNOWN"]

# Evidence rows this module treats as real outcome signal for an
# implementation -- the SAME vocabulary failure_handlers.DEMOTION_EVIDENCE_TYPES
# uses for procedures, reused verbatim rather than reinvented (evidence.py's
# target_type CHECK already includes 'implementation').
_OUTCOME_EVIDENCE_TYPES: tuple[str, ...] = ("execution_result", "reproduction")
_OUTCOME_STREAM_LIMIT = 5000


@dataclass
class RequirementCheck:
    """One deterministic constraint evaluated against one candidate.
    `detail` is always a real, human-readable reason -- never blank --
    since this is the thing a rejection explanation actually shows."""
    name: str
    state: RequirementState
    detail: str


@dataclass
class ScoreComponent:
    """One term of the ranking score, kept individually so the total is
    always explainable, not just a number."""
    name: str
    value: float
    weight: float

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class RankedImplementation:
    implementation: dict
    eligible: bool
    checks: list[RequirementCheck]
    score: Optional[float]
    components: list[ScoreComponent] = field(default_factory=list)

    @property
    def rejection_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.state == "HARD_FALSE"]


@dataclass
class SelectionResult:
    """The full, disclosed selection trace (directive Sec 10: "Persist/
    expose: candidates considered, filtered candidates, rejection reasons,
    score components, chosen Implementation, rationale")."""
    goal: str
    candidates_considered: list[dict]
    ranked: list[RankedImplementation]
    chosen: Optional[dict]
    rationale: str

    @property
    def filtered_out(self) -> list[RankedImplementation]:
        return [r for r in self.ranked if not r.eligible]

    @property
    def survivors(self) -> list[RankedImplementation]:
        return [r for r in self.ranked if r.eligible]


# Default weights -- a starting point, not a claimed-correct calibration
# (directive Sec 10 explicitly defers a learned ranker; these are named,
# inspectable constants a caller can override, never hidden inside the
# scoring function). "cost" (Prompt 2 Sec 11, 2026-09-16) is the newest
# and least-calibrated of these -- kept deliberately low (below
# verified/success_rate) so a cheap-but-unproven implementation cannot
# outrank a proven one; to be finetuned once real telemetry accumulates
# across more implementations (founder direction: "we'll finetune these
# later").
DEFAULT_WEIGHTS: dict[str, float] = {
    "verified": 1.5,
    "success_rate": 2.0,
    "stealth_hosted_preference": 0.5,
    "scope_match": 1.0,
    "cost": 0.5,
}

# Sec 11's cost component, both judgment calls disclosed rather than
# presented as calibrated:
#   - expected_wall_seconds at which the cost score is exactly halved.
#     60.0 is a starting guess with no empirical basis yet -- revisit
#     once real telemetry distributions exist across executor kinds
#     (an LSP call and an LLM call differ by 1-2 orders of magnitude in
#     wall time).
_COST_HALF_LIFE_SECONDS = 60.0
# Ranking makes an automatic, consequential DECISION from this number
# (unlike a disclosed CostEstimate a human/agent can weigh against its
# own confidence label) -- gated more conservatively than the (ungated)
# success_rate component above, at goal_cost.py's own "empirical"
# threshold, so one lucky/unlucky early run cannot swing a routing
# decision. Below this many real samples, the cost component is omitted
# entirely (neutral -- same treatment a zero-sample implementation
# already gets), never penalized or rewarded on noise.
_COST_MIN_SAMPLES_FOR_RANKING = 5


def evaluate_requirements(implementation: dict, context: dict) -> list[RequirementCheck]:
    """The deterministic hard-constraint pass (directive Sec 9). Every
    check appends exactly one RequirementCheck -- a requirement the
    implementation/context pair has no opinion on is simply omitted, not
    stamped UNKNOWN for its own sake (an implementation that declares no
    resource_requirements at all has nothing to be UNKNOWN about).

    `context` keys this function understands (all optional -- an absent
    key never turns a check into HARD_FALSE, per directive Sec 9's "do not
    treat missing evidence as false"):
      - allowed_execution_locations: list[str] | None
      - available_credentials: list[str] (its PRESENCE as a key, even
        empty, means the caller positively enumerated what's available)
      - required_scope_type: str | None
      - privacy_policy: "no_third_party" | None
      - available_resources: dict[str, Any] | None
    """
    checks: list[RequirementCheck] = []

    status = implementation.get("status")
    if status != "active":
        checks.append(RequirementCheck("lifecycle", "HARD_FALSE", f"status={status!r}, not 'active'"))
    else:
        checks.append(RequirementCheck("lifecycle", "SATISFIABLE", "status='active'"))

    loc = implementation.get("execution_location")
    allowed_locations = context.get("allowed_execution_locations")
    if allowed_locations is not None:
        if loc is None:
            checks.append(RequirementCheck(
                "execution_location", "UNKNOWN",
                "caller restricts execution_location but implementation does not declare one",
            ))
        elif loc not in allowed_locations:
            checks.append(RequirementCheck(
                "execution_location", "HARD_FALSE",
                f"execution_location={loc!r} not in allowed {list(allowed_locations)!r}",
            ))
        else:
            checks.append(RequirementCheck("execution_location", "SATISFIABLE", f"execution_location={loc!r} allowed"))

    privacy_policy = context.get("privacy_policy")
    if privacy_policy == "no_third_party" and loc == "third_party_hosted":
        checks.append(RequirementCheck(
            "privacy", "HARD_FALSE",
            "execution_location='third_party_hosted' forbidden by privacy_policy='no_third_party'",
        ))

    required_scope_type = context.get("required_scope_type")
    scope_type = implementation.get("scope_type")
    if required_scope_type is not None:
        if scope_type is None:
            checks.append(RequirementCheck("scope", "SATISFIABLE", "implementation is unscoped, no conflict"))
        elif scope_type != required_scope_type:
            checks.append(RequirementCheck(
                "scope", "HARD_FALSE",
                f"scope_type={scope_type!r} != required {required_scope_type!r}",
            ))
        else:
            checks.append(RequirementCheck("scope", "SATISFIABLE", f"scope_type={scope_type!r} matches"))

    auth_req = implementation.get("auth_requirements") or {}
    required_credentials = set(auth_req.get("credentials") or [])
    if required_credentials:
        if "available_credentials" not in context:
            checks.append(RequirementCheck(
                "auth", "UNKNOWN",
                f"requires credentials {sorted(required_credentials)!r}; caller did not declare availability",
            ))
        else:
            missing = required_credentials - set(context.get("available_credentials") or [])
            if missing:
                checks.append(RequirementCheck("auth", "HARD_FALSE", f"missing credentials: {sorted(missing)!r}"))
            else:
                checks.append(RequirementCheck("auth", "SATISFIABLE", "required credentials available"))

    resource_req = implementation.get("resource_requirements") or {}
    if resource_req:
        available_resources = context.get("available_resources")
        if available_resources is None:
            checks.append(RequirementCheck(
                "resources", "UNKNOWN",
                f"declares resource_requirements {sorted(resource_req)!r}; caller did not declare availability",
            ))
        else:
            unresolved = [k for k in resource_req if k not in available_resources]
            mismatched = {
                k: (resource_req[k], available_resources[k])
                for k in resource_req
                if k in available_resources and available_resources[k] != resource_req[k]
            }
            if mismatched:
                checks.append(RequirementCheck("resources", "HARD_FALSE", f"resource mismatch: {mismatched!r}"))
            elif unresolved:
                checks.append(RequirementCheck(
                    "resources", "UNKNOWN", f"unresolved resource requirement(s): {sorted(unresolved)!r}",
                ))
            else:
                checks.append(RequirementCheck("resources", "SATISFIABLE", "resource requirements match"))

    return checks


def is_eligible(checks: list[RequirementCheck]) -> bool:
    """A candidate is disqualified by ANY HARD_FALSE -- SOFT/SATISFIABLE/
    UNKNOWN never disqualify (directive Sec 9's own state names)."""
    return not any(c.state == "HARD_FALSE" for c in checks)


def _score(
    implementation: dict, context: dict, success_rate: Optional[float], weights: dict[str, float],
    cost_stats: Optional[ImplementationExecutionStats] = None,
) -> list[ScoreComponent]:
    components: list[ScoreComponent] = []
    verified = 1.0 if implementation.get("verification_status") == "verified" else 0.0
    components.append(ScoreComponent("verified", verified, weights["verified"]))

    if success_rate is not None:
        components.append(ScoreComponent("success_rate", success_rate, weights["success_rate"]))

    hosted_pref = 1.0 if implementation.get("execution_location") == "stealth_hosted" else 0.0
    components.append(ScoreComponent("stealth_hosted_preference", hosted_pref, weights["stealth_hosted_preference"]))

    preferred_scope = context.get("preferred_scope_type")
    scope_match = 1.0 if preferred_scope is not None and implementation.get("scope_type") == preferred_scope else 0.0
    components.append(ScoreComponent("scope_match", scope_match, weights["scope_match"]))

    # Prompt 2 Sec 11: cost-informed ranking, sourced from the real
    # execution-telemetry ledger (execution_telemetry.py) -- a DIFFERENT
    # signal than `success_rate` above (that one comes from the older
    # `evidence` table via compute_capability, never conflated with this
    # one). Gated at `_COST_MIN_SAMPLES_FOR_RANKING` real samples --
    # below that, omitted entirely (neutral, same as zero samples),
    # never penalized on a noisy single data point.
    if cost_stats is not None and cost_stats.sample_count >= _COST_MIN_SAMPLES_FOR_RANKING:
        expected_wall = expected_value(cost_stats.mean_wall_seconds, expected_attempts(cost_stats.success_rate))
        if expected_wall is not None:
            cost_score = _COST_HALF_LIFE_SECONDS / (_COST_HALF_LIFE_SECONDS + expected_wall)
            components.append(ScoreComponent("cost", cost_score, weights["cost"]))

    return components


async def _success_rates(pool: asyncpg.Pool, implementation_ids: list[str]) -> dict[str, Optional[float]]:
    """Batched, reusing the SAME evidence-stream capability recompute
    failure_handlers.capability_for_stream applies to procedures --
    evidence.target_type already includes 'implementation' (db/24), so
    this is the identical query shape against a different target_type,
    not a second capability mechanism."""
    if not implementation_ids:
        return {}
    types_sql = ", ".join(f"'{t}'" for t in _OUTCOME_EVIDENCE_TYPES)
    rows = await pool.fetch(
        f"""
        SELECT target_id, outcome_status, context_key, independence_group
        FROM evidence
        WHERE target_type = 'implementation'
          AND target_id = ANY($1::uuid[])
          AND t_invalid IS NULL
          AND evidence_type IN ({types_sql})
          AND outcome_status IN ('success', 'failure')
        ORDER BY t_created ASC, id ASC
        LIMIT {int(_OUTCOME_STREAM_LIMIT)}
        """,
        implementation_ids,
    )
    by_id: dict[str, list[Any]] = {}
    for row in rows:
        by_id.setdefault(str(row["target_id"]), []).append(row)

    rates: dict[str, Optional[float]] = {}
    for impl_id in implementation_ids:
        stream = by_id.get(impl_id, [])
        if not stream:
            rates[impl_id] = None
            continue
        outcomes = [
            OutcomeRecord(
                success=(r["outcome_status"] == "success"),
                environment=r["context_key"] or "(unrecorded)",
                independence_group=r.get("independence_group"),
            )
            for r in stream
        ]
        record = compute_capability(
            outcomes,
            CapabilityScope(
                task=f"implementation:{impl_id}",
                state_signature="(cumulative evidence stream)",
                environment="(aggregated over recorded contexts)",
                input_signature="(all recorded inputs)",
                evaluation_criterion="outcome_status == 'success'",
            ),
        )
        rates[impl_id] = record.p_estimate
    return rates


async def rank_candidates(
    pool: asyncpg.Pool, candidates: list[dict], *, goal: str, context: Optional[dict] = None,
    weights: Optional[dict[str, float]] = None,
) -> SelectionResult:
    """The real Sec 8-10 ranking core: deterministic hard-constraint
    filter -> explainable rank over survivors, given an ALREADY-fetched
    candidate list. Factored out of `select_implementation_for_goal` so a
    caller with its own lookup (e.g. the goal_id-based FK lookup
    `select_implementation_for_goal_id` uses, or the recursive Goal
    compiler resolving a specific candidate set) gets the identical
    filter/rank/explain logic -- one ranking mechanism, not a second one
    per lookup strategy (Rule 6).

    Returns the FULL trace, not just a winner (directive Sec 10) --
    `.stealth/run.md`'s selection-rationale line and the MCP
    `explain_implementation_selection` surface both read straight off
    this, not recompute it. `chosen` is `None` (never a fabricated pick)
    when either `candidates` is empty or every candidate was
    disqualified -- the `rationale` string says which, honestly.
    """
    context = context or {}
    weights = weights or DEFAULT_WEIGHTS

    if not candidates:
        return SelectionResult(
            goal=goal, candidates_considered=[], ranked=[], chosen=None,
            rationale=f"no registered, active implementation has goal={goal!r} -- honest empty result, not a fabricated candidate",
        )

    success_rates = await _success_rates(pool, [str(c["id"]) for c in candidates])
    cost_stats = await implementation_execution_stats_batch(pool, [str(c["id"]) for c in candidates])

    ranked: list[RankedImplementation] = []
    for impl in candidates:
        checks = evaluate_requirements(impl, context)
        eligible = is_eligible(checks)
        if eligible:
            components = _score(
                impl, context, success_rates.get(str(impl["id"])), weights, cost_stats.get(str(impl["id"])),
            )
            score = sum(c.contribution for c in components)
        else:
            components = []
            score = None
        ranked.append(RankedImplementation(
            implementation=impl, eligible=eligible, checks=checks, score=score, components=components,
        ))

    # Stable sort: eligible-first, highest score first; ineligible entries
    # keep candidate (recency) order among themselves, never reshuffled.
    ranked.sort(key=lambda r: (not r.eligible, -(r.score or 0.0)))

    survivors = [r for r in ranked if r.eligible]
    if not survivors:
        return SelectionResult(
            goal=goal, candidates_considered=candidates, ranked=ranked, chosen=None,
            rationale=(
                f"{len(candidates)} candidate(s) found for goal={goal!r} but every one failed a hard "
                f"constraint -- see each RankedImplementation.rejection_reasons"
            ),
        )

    winner = survivors[0]
    reasons = ", ".join(f"{c.name}={c.value:.2f}*{c.weight:.2f}" for c in winner.components) or "no scoring signals available"
    rationale = (
        f"chosen {winner.implementation.get('name')!r} ({winner.implementation['id']}) among "
        f"{len(survivors)} eligible / {len(candidates)} considered for goal={goal!r}: "
        f"score={winner.score:.3f} ({reasons})"
    )
    return SelectionResult(
        goal=goal, candidates_considered=candidates, ranked=ranked, chosen=winner.implementation, rationale=rationale,
    )


async def select_implementation_for_goal(
    pool: asyncpg.Pool,
    goal: str,
    *,
    context: Optional[dict] = None,
    scope: AccessScope,
    weights: Optional[dict[str, float]] = None,
) -> SelectionResult:
    """Structured lookup by free-text `implementations.goal` -> `rank_candidates`.
    See that function's own docstring for the full filter/rank/explain
    contract this preserves unchanged from before the refactor."""
    candidates = await list_implementations_by_goal(pool, goal, scope=scope)
    return await rank_candidates(pool, candidates, goal=goal, context=context, weights=weights)


async def select_implementation_for_goal_id(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    context: Optional[dict] = None,
    scope: AccessScope,
    weights: Optional[dict[str, float]] = None,
) -> SelectionResult:
    """Structured lookup by the real `implementations.goal_id` FK
    (backend/db/83_goals.sql, `ingestion` lane) -> `rank_candidates`. A
    precise, id-based sibling of `select_implementation_for_goal`'s
    free-text lookup -- prefer this one whenever a real Goal id is
    already known (the recursive Goal compiler's own case), since it
    cannot miss on a phrasing difference the way exact-string matching
    can."""
    from app.services.access import visibility_predicate

    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    rows = await pool.fetch(
        f"SELECT * FROM implementations WHERE goal_id = $1::uuid AND status = 'active' AND {vis_sql}",
        goal_id, *vis_params,
    )
    candidates = [dict(r) for r in rows]
    return await rank_candidates(pool, candidates, goal=goal_id, context=context, weights=weights)
