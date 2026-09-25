"""
Contribution + verification + ranking + Credits REST surface (migration
102). Thin, same shape as app/api/goals.py: hydrates scope/pool and
delegates to app.economy.*. No business logic lives here.

Only endpoints for capabilities that are actually implemented are exposed
here -- Goals, Procedure/Benchmark detail, Runs/Evidence and
Contributor profile already have their own routers (goals.py,
procedures.py, runs.py, contributors.py) and are not duplicated.

HARDENING PASS (audit findings D1/D3/D6/D7/D25): every economically
meaningful write now requires `require_authenticated_user` (the same
mechanism runs.py/agent_store.py/approval.py already use) and derives the
acting identity from `principal.subject` -- request bodies no longer carry
`submitted_by`/`executed_by`/`reviewed_by`/`created_by` at all, so there is
nothing for a client to spoof. The three Credits/Standing GET endpoints now
require authentication and restrict reads to the caller's own data unless
they hold `ADMIN_OPS`. Submission and usage-event writes are rate-limited
via the existing `app.services.governance.RateLimiter`, keyed by the
authenticated subject, never a client-supplied string.
"""
from __future__ import annotations

from datetime import timedelta
import inspect
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user, require_scopes
from app.economy import constants as c
from app.economy import credits as credits_service
from app.economy import ranking as ranking_service
from app.economy import standing as standing_service
from app.economy import submissions as submissions_service
from app.economy import verification as verification_service
from app.services import auth_context as _ac
from app.services.access import AccessScope, TenantScope
from app.services.governance import RateLimit, RateLimiter, RateLimitExceeded
from app.services.product_model import get_goal_for_product, list_goal_solutions

router = APIRouter(prefix="/v1/economy", tags=["economy"])


async def get_pool(request: Request):
    return request.app.state.pool


_WRITE_RATE_LIMITS = {
    "/v1/economy/procedure-submissions": RateLimit(max_requests=c.SUBMISSION_RATE_LIMIT_MAX, window=timedelta(hours=c.SUBMISSION_RATE_LIMIT_WINDOW_HOURS)),
    "/v1/economy/benchmark-submissions": RateLimit(max_requests=c.SUBMISSION_RATE_LIMIT_MAX, window=timedelta(hours=c.SUBMISSION_RATE_LIMIT_WINDOW_HOURS)),
    "/v1/economy/usage-events": RateLimit(max_requests=c.USAGE_EVENT_RATE_LIMIT_MAX, window=timedelta(hours=c.USAGE_EVENT_RATE_LIMIT_WINDOW_HOURS)),
}


async def _authenticated_and_rate_limited(
    request: Request, principal: AuthenticatedPrincipal = Depends(require_authenticated_user), pool=Depends(get_pool),
) -> AuthenticatedPrincipal:
    """Identity + abuse control for the write endpoints (§12). Keyed by the
    verified subject -- never a request-body field -- so a client cannot
    reset its own rate-limit window by claiming a different identity."""
    try:
        await RateLimiter(pool, limits=_WRITE_RATE_LIMITS).check_and_record(f"user:{principal.subject}", request.url.path)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}) from exc
    return principal


def _require_self_or_admin(principal: AuthenticatedPrincipal, contributor_id: str) -> None:
    """Private economic data (§2): a caller may read only their own Credits/
    Standing/history unless they hold ADMIN_OPS. Enforced server-side --
    never inferred from a hidden frontend button."""
    if principal.subject == contributor_id:
        return
    if principal.has_scope(_ac.ADMIN_OPS):
        return
    raise HTTPException(status_code=403, detail="you may only view your own economic data")


def _tenant_scope(principal: AuthenticatedPrincipal) -> TenantScope:
    if principal.org_ids:
        return TenantScope.for_tenant(principal.org_ids[0])
    return TenantScope.commons()


def _supported_kwargs(function: Any, values: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return values
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return values
    return {key: value for key, value in values.items() if key in parameters}


# ---- request bodies --------------------------------------------------------
# No identity fields (submitted_by / executed_by / reviewed_by / created_by)
# on any of these -- the acting identity is always server-derived from the
# authenticated principal (see each route below). This is intentional, not
# an oversight: adding one back would reopen the exact hole the hardening
# pass closed.
class ProcedureSubmissionIn(BaseModel):
    goal_id: str
    submission_type: str
    name: str
    steps: list[Any] = Field(default_factory=list)
    rationale: Optional[str] = None
    preconditions: list[Any] = Field(default_factory=list)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    expected_effects: list[Any] = Field(default_factory=list)
    postconditions: list[Any] = Field(default_factory=list)
    failure_conditions: list[Any] = Field(default_factory=list)
    existing_evidence: list[Any] = Field(default_factory=list)
    previous_executions: list[Any] = Field(default_factory=list)
    known_failure_modes: list[Any] = Field(default_factory=list)
    applicability_context: dict[str, Any] = Field(default_factory=dict)
    constraints: list[Any] = Field(default_factory=list)
    implementation_requirements: dict[str, Any] = Field(default_factory=dict)
    supporting_evidence: list[Any] = Field(default_factory=list)
    parent_procedure_row_id: Optional[str] = None


class SubmissionReviewIn(BaseModel):
    decision: str  # 'accepted' | 'rejected' | 'needs_review'
    note: Optional[str] = None


class BenchmarkSubmissionIn(BaseModel):
    goal_id: str
    name: str
    description: Optional[str] = None
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    failure_criteria: list[Any] = Field(default_factory=list)
    scope_conditions: list[Any] = Field(default_factory=list)
    invariants: list[Any] = Field(default_factory=list)
    verification_method: dict[str, Any] = Field(default_factory=dict)
    environment_specification: dict[str, Any] = Field(default_factory=dict)
    comparison_policy: dict[str, Any] = Field(default_factory=dict)


class UsageEventIn(BaseModel):
    procedure_row_id: str
    # outcome_state / verification_layer are NOT client-settable -- the
    # server derives them from execution_run_id + evidence_id (see
    # app.economy.verification._verify_execution_chain). Omitting either
    # produces an 'unknown' usage event, which is still real and recorded,
    # just not reward-eligible.
    execution_run_id: Optional[str] = None
    evidence_id: Optional[str] = None
    benchmark_id: Optional[str] = None
    context_key: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClawbackIn(BaseModel):
    reason_text: str


# ---- Procedure submissions (§1, §11) --------------------------------------
@router.post("/procedure-submissions", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def create_procedure_submission(
    body: ProcedureSubmissionIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(_authenticated_and_rate_limited),
) -> dict[str, Any]:
    try:
        return await submissions_service.create_procedure_submission(
            pool,
            actor_subject=principal.subject,
            access_scope=principal.access_scope(),
            tenant_scope=_tenant_scope(principal),
            **body.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/procedure-submissions")
async def list_procedure_submissions(
    goal_id: Optional[str] = None, status: Optional[str] = None, submitted_by: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200), pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    return {
        "submissions": await submissions_service.list_procedure_submissions(
            pool, scope=principal.access_scope(), goal_id=goal_id, status=status,
            submitted_by=submitted_by, limit=limit, tenant_scope=_tenant_scope(principal),
        )
    }


@router.get("/procedure-submissions/{submission_id}")
async def get_procedure_submission(
    submission_id: str, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    row = await submissions_service.get_procedure_submission(
        pool, submission_id, scope=principal.access_scope(), tenant_scope=_tenant_scope(principal)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="procedure submission not found")
    return row


@router.post("/procedure-submissions/{submission_id}/review", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_PUBLISH))])
async def review_procedure_submission(
    submission_id: str, body: SubmissionReviewIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    try:
        values = {
            "submission_id": submission_id,
            "decision": body.decision,
            "actor_subject": principal.subject,
            "note": body.note,
            "access_scope": principal.access_scope(),
            "tenant_scope": _tenant_scope(principal),
        }
        values = _supported_kwargs(submissions_service.review_procedure_submission, values)
        return await submissions_service.review_procedure_submission(pool, **values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---- Benchmark submissions (§2, §11) ---------------------------------------
@router.post("/benchmark-submissions", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_WRITE))])
async def create_benchmark_submission(
    body: BenchmarkSubmissionIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(_authenticated_and_rate_limited),
) -> dict[str, Any]:
    try:
        return await submissions_service.create_benchmark_submission(
            pool,
            actor_subject=principal.subject,
            access_scope=principal.access_scope(),
            tenant_scope=_tenant_scope(principal),
            **body.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/benchmark-submissions")
async def list_benchmark_submissions(
    goal_id: Optional[str] = None, status: Optional[str] = None, submitted_by: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200), pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    return {
        "submissions": await submissions_service.list_benchmark_submissions(
            pool, scope=principal.access_scope(), goal_id=goal_id, status=status,
            submitted_by=submitted_by, limit=limit, tenant_scope=_tenant_scope(principal),
        )
    }


@router.get("/benchmark-submissions/{submission_id}")
async def get_benchmark_submission(
    submission_id: str, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    row = await submissions_service.get_benchmark_submission(
        pool, submission_id, scope=principal.access_scope(), tenant_scope=_tenant_scope(principal)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="benchmark submission not found")
    return row


@router.post("/benchmark-submissions/{submission_id}/review", dependencies=[Depends(require_scopes(_ac.KNOWLEDGE_PUBLISH))])
async def review_benchmark_submission(
    submission_id: str, body: SubmissionReviewIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    try:
        values = {
            "submission_id": submission_id,
            "decision": body.decision,
            "actor_subject": principal.subject,
            "note": body.note,
            "access_scope": principal.access_scope(),
            "tenant_scope": _tenant_scope(principal),
        }
        values = _supported_kwargs(submissions_service.review_benchmark_submission, values)
        return await submissions_service.review_benchmark_submission(pool, **values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---- Usage events / verification state (§3, §5, §11) -----------------------
@router.post("/usage-events", dependencies=[Depends(require_scopes(_ac.EXECUTION_RUN))])
async def record_usage_event(
    body: UsageEventIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(_authenticated_and_rate_limited),
) -> dict[str, Any]:
    try:
        row = await verification_service.record_usage_event(
            pool,
            executor_subject=principal.subject,
            access_scope=principal.access_scope(),
            tenant_scope=_tenant_scope(principal),
            **body.model_dump(),
        )
    except verification_service.VerificationMismatch as e:
        raise HTTPException(status_code=422, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="procedure not found")
    if row["outcome_state"] == "verified_success" and not row["is_self_use"]:
        row = dict(row)
        row["reward_events"] = await credits_service.reward_verified_reuse(pool, usage_event=row)
    return row


@router.get("/procedures/{procedure_row_id}/usage-events")
async def list_procedure_usage_events(
    procedure_row_id: str, limit: int = Query(50, ge=1, le=200), pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    rows = await submissions_service.list_procedure_usage_events(
        pool, procedure_row_id=procedure_row_id, scope=principal.access_scope(),
        tenant_scope=_tenant_scope(principal), limit=limit,
    )
    return {"usage_events": rows}


# ---- Contextual ranking (§5/§6 of the harden+consolidate directive) -------
# ONE canonical ranking implementation: app.economy.ranking delegates to the
# centralized Bayesian Procedure ranking service.
@router.get("/goals/{goal_id}/procedures/ranked")
async def ranked_procedures_for_goal(
    goal_id: str, context_key: Optional[str] = None, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    # B1 hardening, defense in depth: only 'active' associations (set
    # exclusively by the canonical review workflow, never by the public
    # /v1/solutions/associate route) are ranked.
    solutions = await list_goal_solutions(pool, goal_id, scope=scope)
    procedure_ids = [str(s["target_id"]) for s in solutions if s["target_table"] == "procedures" and s.get("status") == "active"]
    ranked = await ranking_service.rank_procedures_for_goal(pool, procedure_ids, scope=scope, context_key=context_key)
    return {
        "goal_id": goal_id, "context_key": context_key,
        "note": "ranked for this goal's recorded evidence in a matching context -- not a universal best; "
                "a specialized procedure can rank highly within its own niche",
        "ranked": ranked,
    }


# ---- Contributor Standing / Credits (§6, §7, §8, §11) -----------------------
# All three require authentication and are self-or-admin only (§2) -- a
# contributor's Credits balance/history and Standing are private economic
# data, not a public leaderboard.
@router.get("/contributors/{contributor_id}/standing")
async def contributor_standing(
    contributor_id: str, pool=Depends(get_pool), principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    _require_self_or_admin(principal, contributor_id)
    return await standing_service.compute_standing(pool, contributor_id)


@router.get("/contributors/{contributor_id}/credits")
async def contributor_credits_balance(
    contributor_id: str, pool=Depends(get_pool), principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    _require_self_or_admin(principal, contributor_id)
    return {"contributor_id": contributor_id, "balance": await credits_service.get_balance(pool, contributor_id)}


@router.get("/contributors/{contributor_id}/credits/history")
async def contributor_credits_history(
    contributor_id: str, limit: int = Query(50, ge=1, le=200), pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    _require_self_or_admin(principal, contributor_id)
    return {"contributor_id": contributor_id, "events": await credits_service.get_history(pool, contributor_id, limit=limit)}


@router.post("/credits/{event_id}/clawback", dependencies=[Depends(require_scopes(_ac.ADMIN_OPS))])
async def clawback_credit_event(
    event_id: str, body: ClawbackIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict[str, Any]:
    try:
        return await credits_service.clawback(pool, event_id=event_id, reason_text=body.reason_text, created_by=principal.subject)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---- Credit commitments to Goals (escrow bounty, migration 119) -------------
class CommitmentIn(BaseModel):
    credits: int = Field(ge=1, le=1_000_000)
    idempotency_key: str = Field(min_length=1, max_length=200)


@router.post("/goals/{goal_id}/commitments", status_code=201)
async def commit_credits_to_goal(
    goal_id: str, body: CommitmentIn, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(_authenticated_and_rate_limited),
) -> dict[str, Any]:
    """Lock Credits on a Goal you can see. Paid to the contributor of the Procedure
    that resolves it; withdraw any time before that. Identity is the caller's."""
    from app.economy import commitments

    try:
        return await commitments.commit(
            pool, goal_id=goal_id, contributor_id=principal.subject, credits=body.credits,
            idempotency_key=body.idempotency_key, access_scope=principal.access_scope())
    except commitments.GoalNotCommittable as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc
    except commitments.InsufficientCredits as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except commitments.CommitmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/goals/{goal_id}/commitments/{commitment_id}")
async def withdraw_commitment(
    goal_id: str, commitment_id: str, pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(_authenticated_and_rate_limited),
) -> dict[str, Any]:
    from app.economy import commitments

    try:
        return await commitments.withdraw(pool, commitment_id=commitment_id, contributor_id=principal.subject)
    except commitments.CommitmentError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc


@router.get("/goals/{goal_id}/commitments")
async def goal_commitment_totals(
    goal_id: str, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """Demand on this Goal (open commitments only): on the Goal itself and
    aggregated over its accepted descendants you can see. Totals only; your own
    commitments are listed when you are signed in."""
    from app.economy import commitments

    goal = await get_goal_for_product(pool, goal_id, scope=scope)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    demand = (await commitments.goal_demand(pool, [goal_id], access_scope=scope)).get(goal_id, {})
    mine = await commitments.my_commitments(pool, goal_id=goal_id, contributor_id=scope.viewer_id) \
        if scope.viewer_id else []
    return {"goal_id": goal_id, "resolved": goal.get("resolved_at") is not None, **demand, "mine": mine}


# ---- Goal-level contributors (§11) -----------------------------------------
@router.get("/goals/{goal_id}/contributors")
async def goal_contributors(goal_id: str, pool=Depends(get_pool), scope: AccessScope = Depends(get_scope)) -> dict[str, Any]:
    goal = await get_goal_for_product(pool, goal_id, scope=scope)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    rows = await pool.fetch(
        """
        SELECT * FROM (
            SELECT contributor_id,
                   count(*) FILTER (WHERE kind = 'procedure') AS procedures,
                   count(*) FILTER (WHERE kind = 'improvement') AS improvements,
                   count(*) FILTER (WHERE kind = 'benchmark') AS benchmarks
            FROM (
                SELECT submitted_by AS contributor_id, 'procedure' AS kind FROM procedure_submissions
                WHERE goal_id = $1 AND status = 'accepted' AND submission_type = 'new'
                UNION ALL
                SELECT submitted_by, 'improvement' FROM procedure_submissions
                WHERE goal_id = $1 AND status = 'accepted' AND submission_type = 'improvement'
                UNION ALL
                SELECT submitted_by, 'benchmark' FROM benchmark_submissions
                WHERE goal_id = $1 AND status = 'accepted'
            ) contributions
            GROUP BY contributor_id
        ) totals
        -- Postgres only allows a BARE output alias in ORDER BY, not one used
        -- inside a larger expression -- it tries to resolve `procedures` as
        -- an INPUT column instead (and this schema happens to have a real
        -- `procedures` table, which is why the error name is misleading).
        -- Wrapping in a subquery makes the aliases real input columns here.
        ORDER BY procedures + improvements + benchmarks DESC
        """,
        goal_id,
    )
    return {"goal_id": goal_id, "note": "contribution to this goal specifically, not a global ranking", "contributors": [dict(r) for r in rows]}
