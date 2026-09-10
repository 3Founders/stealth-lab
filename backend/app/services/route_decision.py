"""
`auto` routing (MCP hardening plan, Part III B1/B2). `find_best_way`'s
`mode="auto"` used to mean "try tier 1, fall through to tier 2 (a real
sandboxed agent run) whenever tier 1 found nothing" -- with no formal
result state, no persisted decision, and no distinction between "no
procedure matched because none is applicable" and "no procedure matched
because a decision-critical precondition is simply UNKNOWN" (the second
case must ask, not silently execute).

This module adds exactly two things, both additive -- nothing here
changes applicability.py's cascade semantics, find_applicable_procedures'
behavior, or any existing caller:

1. `classify_intent()` -- a deterministic, keyword-based classifier
   (assist | plan | execute | ambiguous), so routing is a testable
   policy table, not a model guess (B2: "Routing rules are
   deterministic/testable at the policy level").
2. `decide_route()` -- normalizes inputs, classifies intent, evaluates
   the best candidate's applicability (reusing
   applicability.find_applicable_procedures / diagnose_candidates,
   never re-implementing the cascade), classifies any precondition gap
   as UNKNOWN or FALSE (reusing app.services.state.project_state,
   never changing check_hard_constraints' own CWA collapse -- that
   collapse is a deliberate, heavily-relied-upon design choice
   documented in applicability.py's own module docstring, kept as-is
   for every one of its ~40 existing callers), and returns one of the
   six formal RouteDecision states.

Every decision is persisted via `persist_route_decision()` to the
`route_decisions` table (migration 50) -- "routing becomes observable
and testable" (B2), not merely a returned string nobody can inspect
later.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.services.access import AccessScope
from app.services.applicability import ApplicabilityResult, diagnose_candidates
from app.services.state import project_state

ROUTE_STATES: tuple[str, ...] = (
    "needs_clarification",
    "no_applicable_procedure",
    "assist",
    "plan_ready",
    "execution_ready",
    "refused",
)

INTENTS: tuple[str, ...] = ("assist", "plan", "execute", "ambiguous")

# Deterministic, ordered (most-specific-first) keyword families. These are
# a policy table, not an ML model -- exactly B2's "deterministic/testable
# at the policy level" requirement. Extending this list is a reviewable
# one-line change, not a retraining exercise.
_PLAN_PATTERNS: tuple[str, ...] = (
    "give me a plan", "draft a plan", "create a plan", "make a plan",
    "plan out", "plan for", "outline the steps", "outline a plan",
    "break down", "break this down", "decompose",
)
_ASSIST_PATTERNS: tuple[str, ...] = (
    "how should i", "how do i", "how can i", "what's the best way",
    "what is the best way", "best way to", "should i", "recommend",
    "any advice", "any suggestions", "what do you think", "is it better to",
)
_EXECUTE_VERBS: tuple[str, ...] = (
    "fix", "implement", "add", "refactor", "resolve", "patch", "write",
    "build", "create", "update", "remove", "delete", "migrate", "upgrade",
    "debug", "solve", "change", "rename", "replace", "optimize",
)
_EXECUTE_VERB_RE = re.compile(
    r"^(?:please\s+)?(" + "|".join(_EXECUTE_VERBS) + r")\b", re.IGNORECASE,
)


def classify_intent(task_description: str, mode: str) -> str:
    """
    Deterministic intent classification (B1's routing table). `mode`
    values other than "auto" are already explicit requests -- they are
    translated directly rather than re-derived from text, since a caller
    who passed mode="full_run" has already told us the intent regardless
    of phrasing.
    """
    if mode == "lookup_only":
        return "assist"
    if mode == "plan_only":
        return "plan"
    if mode == "full_run":
        return "execute"

    text = (task_description or "").strip().lower()
    if any(p in text for p in _PLAN_PATTERNS):
        return "plan"
    if any(p in text for p in _ASSIST_PATTERNS) or text.endswith("?"):
        return "assist"
    if _EXECUTE_VERB_RE.match(text):
        return "execute"
    return "ambiguous"


def _parse_precondition_constraint(constraint: str) -> Optional[dict]:
    """Reverses check_hard_constraints()'s own
    `f"precondition:subject={s},predicate={p},object={o}"` formatting.
    Returns None for any other constraint name (temporal_validity,
    staleness, availability, verification_state, approval_status, scope,
    exclusions, invariant:*) -- those are never precondition-shaped and
    are never decision-critical-unknown candidates, only genuine
    disqualifications."""
    if not constraint.startswith("precondition:"):
        return None
    body = constraint[len("precondition:"):]
    parts: dict[str, Optional[str]] = {"subject": None, "predicate": None, "object": None}
    for chunk in body.split(","):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        if key in parts:
            parts[key] = None if value == "None" else value
    if not parts["subject"]:
        return None
    return parts


async def classify_precondition_gap(
    pool: asyncpg.Pool,
    *,
    subject: str,
    as_of: datetime,
    access_scope: Optional[AccessScope],
) -> str:
    """
    Additive diagnostic (never consulted by check_hard_constraints()
    itself, which keeps its existing, deliberate CWA collapse for every
    other caller): "unknown" when NO live claim exists for `subject` at
    all -- nothing has ever asserted anything about it, so a failed
    precondition naming it is a genuine gap in knowledge, not a
    violation. "false" when at least one live claim about `subject`
    exists but the specific precondition still doesn't hold -- something
    IS known about this subject, and it disagrees, so this is a real
    disqualification, not a decision-critical unknown.
    """
    claims = await project_state(pool, subjects=[subject], as_of=as_of, scope=access_scope)
    return "unknown" if not claims else "false"


@dataclass
class RouteDecision:
    route: str
    reason: str
    intent: str
    task_description: str
    mode: str
    confidence: Optional[float] = None
    repo_path: Optional[str] = None
    session_id: Optional[str] = None
    workspace_id: Optional[str] = None
    procedure_row_id: Optional[str] = None
    procedure_id: Optional[str] = None
    procedure_version: Optional[int] = None
    applicable: Optional[bool] = None
    failed_constraints: list[str] = field(default_factory=list)
    decision_critical_unknowns: list[dict] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    authorization_detail: dict = field(default_factory=dict)
    requires_repository: bool = False
    requires_confirmation: bool = False
    created_by: Optional[str] = None
    scope_type: Optional[str] = None
    scope_entity_id: Optional[str] = None
    # B1's own pipeline: "retrieve Procedures -> retrieve relevant Claims
    # -> evaluate applicability -> resolve candidate Implementations ->
    # determine missing decision-critical facts -> choose route". Both
    # steps below now genuinely run inside decide_route() (real calls,
    # bounded, never fabricated) -- not persisted to route_decisions
    # (B2's own field list doesn't include them; they belong on
    # find_best_way's OUTPUT per B32), but present on every returned
    # RouteDecision so find_best_way's response builder never has to
    # re-run the same retrieval a second time.
    relevant_claim_refs: list[dict] = field(default_factory=list)
    implementation_candidates: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.route not in ROUTE_STATES:
            raise ValueError(f"invalid route {self.route!r}, must be one of {ROUTE_STATES}")
        if self.intent not in INTENTS:
            raise ValueError(f"invalid intent {self.intent!r}, must be one of {INTENTS}")


async def decide_route(
    pool: asyncpg.Pool,
    *,
    task_description: str,
    mode: str,
    repo_path: Optional[str],
    authorized: bool,
    authorization_detail: dict,
    goal_embedding: Optional[list[float]],
    current_scope: Optional[dict] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    embedding_model_id: Optional[str] = None,
    goal_text: Optional[str] = None,
    environment: Optional[dict] = None,
    session_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    excluded_procedure_ids: Optional[list[str]] = None,
) -> RouteDecision:
    """
    The B1 algorithm, now genuinely running every pipeline step the spec
    names (not just the applicability-adjacent ones): normalize ->
    classify intent -> retrieve candidates -> retrieve relevant Claims ->
    evaluate applicability -> resolve candidate Implementations ->
    identify decision-critical unknowns -> choose route.

    This is a thin wrapper around `_decide_route_core` (the pre-existing
    applicability/intent logic, unchanged) that additionally runs the two
    real retrieval steps B1 names but this module never called before --
    `get_relevant_claims` (B30/B32) and `get_bindings_for_procedure`
    (B23/B24) -- and attaches their REAL results onto the returned
    decision. Wrapped rather than threaded through every one of
    `_decide_route_core`'s six return points because claims retrieval
    does not depend on which branch was taken (it only depends on the
    goal), and implementation-candidate resolution only needs the
    decision's own `procedure_id` once chosen -- both are genuinely
    independent of the routing logic itself, matching the spec's own
    framing of them as pipeline STEPS that inform (not replace) routing,
    not new gating rules layered into the cascade.
    """
    relevant_claim_refs: list[dict] = []
    try:
        from app.services.relevant_claims import get_relevant_claims
        relevant_claim_refs = await get_relevant_claims(
            pool, goal=task_description, top_k=5, access_scope=access_scope,
        )
    except Exception:  # noqa: BLE001 -- claims retrieval is informational;
        # a failure here must never block routing itself (B1's routing
        # logic has its own, separately-tested failure semantics).
        relevant_claim_refs = []

    decision = await _decide_route_core(
        pool, task_description=task_description, mode=mode, repo_path=repo_path,
        authorized=authorized, authorization_detail=authorization_detail,
        goal_embedding=goal_embedding, current_scope=current_scope,
        invariant_bindings=invariant_bindings, access_scope=access_scope,
        require_verified=require_verified, embedding_model_id=embedding_model_id,
        goal_text=goal_text, environment=environment, session_id=session_id,
        workspace_id=workspace_id, created_by=created_by, scope_type=scope_type,
        scope_entity_id=scope_entity_id, excluded_procedure_ids=excluded_procedure_ids,
    )
    decision.relevant_claim_refs = relevant_claim_refs

    if decision.procedure_id is not None:
        try:
            from app.services.procedure_implementation_bindings import get_bindings_for_procedure
            decision.implementation_candidates = await get_bindings_for_procedure(
                pool, procedure_id=decision.procedure_id, access_scope=access_scope,
            )
        except Exception:  # noqa: BLE001 -- same informational-only discipline as claims above.
            decision.implementation_candidates = []

    return decision


async def _decide_route_core(
    pool: asyncpg.Pool,
    *,
    task_description: str,
    mode: str,
    repo_path: Optional[str],
    authorized: bool,
    authorization_detail: dict,
    goal_embedding: Optional[list[float]],
    current_scope: Optional[dict] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    embedding_model_id: Optional[str] = None,
    goal_text: Optional[str] = None,
    environment: Optional[dict] = None,
    session_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    excluded_procedure_ids: Optional[list[str]] = None,
) -> RouteDecision:
    """
    The applicability/intent core: normalize -> classify intent ->
    retrieve candidates -> evaluate applicability -> identify decision-
    critical unknowns -> choose route. Never runs anything (no sandbox,
    no LLM call, no execution plan) -- this is a pure decision over
    already-available knowledge, exactly the "instantiate it" step B1
    wants BEFORE any commitment to execute.

    `authorized`/`authorization_detail`: computed by the caller (server.py's
    existing `_authorize_repo_execution`), not re-derived here -- this
    module owns routing policy, not auth, per Rule 2 (no second auth
    mechanism).
    """
    intent = classify_intent(task_description, mode)
    environment = environment or {}

    if not authorized:
        return RouteDecision(
            route="refused", reason="repository execution is not authorized for this caller/workspace",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id, authorization_detail=authorization_detail,
            requires_repository=repo_path is not None, environment=environment,
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    matched = await diagnose_candidates(
        pool, goal_embedding=goal_embedding, current_scope=current_scope,
        access_scope=access_scope, require_verified=require_verified,
        invariant_bindings=invariant_bindings, embedding_model_id=embedding_model_id,
        goal_text=goal_text, limit=3, excluded_procedure_ids=excluded_procedure_ids,
    )
    best: Optional[ApplicabilityResult] = matched[0] if matched else None

    if best is not None and best.applicable:
        procedure = best.procedure or {}
        route = "assist" if intent == "assist" else ("plan_ready" if intent == "plan" else "execution_ready")
        return RouteDecision(
            route=route,
            reason=f"applicable procedure matched (intent={intent})",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            confidence=best.similarity_score, session_id=session_id, workspace_id=workspace_id,
            procedure_row_id=str(procedure.get("id")) if procedure.get("id") else None,
            procedure_id=str(procedure.get("procedure_id")) if procedure.get("procedure_id") else None,
            procedure_version=procedure.get("version"), applicable=True,
            environment=environment, authorization_detail=authorization_detail,
            requires_repository=route == "execution_ready" and repo_path is None,
            requires_confirmation=route == "execution_ready",
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    # No applicable procedure survived the cascade. Distinguish "genuinely
    # inapplicable" from "blocked on a decision-critical unknown" using
    # the single best (still-failing) near-miss candidate only -- a
    # candidate ranked below it is a worse match regardless, so it can
    # never be the reason to ask rather than refuse/no-match.
    decision_critical_unknowns: list[dict] = []
    if best is not None and len(best.failed_constraints) == 1:
        precondition = _parse_precondition_constraint(best.failed_constraints[0])
        if precondition is not None:
            gap = await classify_precondition_gap(
                pool, subject=precondition["subject"],
                as_of=datetime.now(timezone.utc), access_scope=access_scope,
            )
            if gap == "unknown":
                decision_critical_unknowns.append(precondition)

    # Intent alone never overrides a decision-critical unknown: asking
    # is required regardless of whether the caller phrased this as a
    # question, a plan request, or an execute request (B1: "Do not
    # return an apparently usable Procedure when applicability is
    # UNKNOWN on a critical precondition" -- symmetrically, do not
    # silently proceed past it either).
    if decision_critical_unknowns:
        procedure = best.procedure or {}
        return RouteDecision(
            route="needs_clarification",
            reason="the single best-matching procedure is blocked on an unknown "
                   "(not violated) precondition -- resolve it before routing further",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id,
            procedure_row_id=str(procedure.get("id")) if procedure.get("id") else None,
            procedure_id=str(procedure.get("procedure_id")) if procedure.get("procedure_id") else None,
            procedure_version=procedure.get("version"), applicable=False,
            failed_constraints=list(best.failed_constraints),
            decision_critical_unknowns=decision_critical_unknowns,
            environment=environment, authorization_detail=authorization_detail,
            requires_repository=repo_path is None,
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    failed_constraints = list(best.failed_constraints) if best is not None else []

    if intent == "assist":
        return RouteDecision(
            route="assist",
            reason="no applicable procedure; informational intent needs no execution",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id, applicable=False,
            failed_constraints=failed_constraints, environment=environment,
            authorization_detail=authorization_detail,
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    if intent == "plan" or (intent == "ambiguous" and repo_path is None):
        return RouteDecision(
            route="no_applicable_procedure",
            reason="no procedure passed the applicability cascade for this goal",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id, applicable=False,
            failed_constraints=failed_constraints, environment=environment,
            authorization_detail=authorization_detail, requires_repository=repo_path is None,
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    # intent in {"execute", "ambiguous"-with-repo_path}: exploration is
    # allowed (B1: "no Procedure but exploration is allowed -> clearly
    # labeled exploratory plan/execution"), but ONLY when a repo_path
    # exists to execute against -- otherwise there is nothing to execute
    # and this degrades to the same no-match answer as above.
    if repo_path is None:
        return RouteDecision(
            route="no_applicable_procedure",
            reason="no procedure passed the applicability cascade, and no repo_path "
                   "was given to run an exploratory execution against",
            intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id, applicable=False,
            failed_constraints=failed_constraints, environment=environment,
            authorization_detail=authorization_detail, requires_repository=True,
            created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

    return RouteDecision(
        route="execution_ready",
        reason="no procedure passed the applicability cascade; exploratory "
               "execution (ad-hoc, unverified) is authorized against the given repo_path",
        intent=intent, task_description=task_description, mode=mode, repo_path=repo_path,
        session_id=session_id, workspace_id=workspace_id, applicable=False,
        failed_constraints=failed_constraints, environment=environment,
        authorization_detail=authorization_detail, requires_confirmation=True,
        created_by=created_by, scope_type=scope_type, scope_entity_id=scope_entity_id,
    )


async def persist_route_decision(pool: asyncpg.Pool, decision: RouteDecision) -> str:
    """INSERT-only (route decisions are an observability record, never
    mutated after the fact -- there is no update_route_decision())."""
    row = await pool.fetchrow(
        """
        INSERT INTO route_decisions (
            route, reason, confidence, intent, task_description, mode, repo_path,
            session_id, workspace_id, procedure_row_id, procedure_id, procedure_version,
            applicable, failed_constraints, decision_critical_unknowns, environment,
            authorization_detail, requires_repository, requires_confirmation, created_by,
            scope_type, scope_entity_id
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
        ) RETURNING id
        """,
        decision.route, decision.reason, decision.confidence, decision.intent,
        decision.task_description, decision.mode, decision.repo_path,
        decision.session_id, decision.workspace_id,
        decision.procedure_row_id, decision.procedure_id, decision.procedure_version,
        # Raw Python objects, NOT json.dumps()'d -- the pool's registered
        # jsonb codec (app/db/session.py::_init_connection) already
        # encodes these; pre-encoding here would double-encode (confirmed
        # live: a manually json.dumps()'d string bound to a jsonb column
        # round-trips as a JSON STRING containing the array text, not a
        # real array -- `SELECT $1::jsonb` with a pre-dumped string vs a
        # raw list proves the difference directly).
        decision.applicable, decision.failed_constraints,
        decision.decision_critical_unknowns, decision.environment,
        decision.authorization_detail, decision.requires_repository,
        decision.requires_confirmation, decision.created_by,
        decision.scope_type, decision.scope_entity_id,
    )
    return str(row["id"])


async def get_route_decision(pool: asyncpg.Pool, route_decision_id: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM route_decisions WHERE id = $1::uuid", route_decision_id)
    return dict(row) if row else None
