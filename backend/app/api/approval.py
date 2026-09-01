"""
Approval endpoints (MVP plan, Section 9).

The approve path does three things in order: record the human decision,
apply the change set, write a VALIDATED_BY edge. If application fails the
whole thing rolls back and the debate stays PENDING_APPROVAL -- an
approval recorded against a change that did not apply would be a false
audit trail, which is worse than no audit trail.

`role` is read but not enforced (Section 12 auth placeholder). Enforcement
arrives with real RBAC at the second-customer trigger; the field exists
now so adding enforcement is a check, not a migration.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.debate.state_machine import DebateStateMachine, IllegalTransition, assert_transition
from app.export.markdown_diff import render_export
from app.models.change import ChangeSet
from app.models.debate import Layer1Result, Scorecard
from app.services.authn import current_actor_id
from app.services.human_participation import DebateNotPendingApproval, add_human_turn
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater

log = logging.getLogger(__name__)


class HumanTurnRequest(BaseModel):
    author: str
    content: str
    action: str = "propose"
    candidate_id: Optional[UUID] = None


class HumanTurnResponse(BaseModel):
    new_scorecards: list[Scorecard]


router = APIRouter(prefix="/v1/approvals", tags=["approval"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.post("/{debate_id}/human-turn", response_model=HumanTurnResponse)
async def submit_human_turn(
    debate_id: UUID, body: HumanTurnRequest, request: Request,
) -> HumanTurnResponse:
    pool = request.app.state.pool
    try:
        scorecards = await add_human_turn(
            pool, debate_id, body.author, body.content,
            action=body.action, candidate_id=body.candidate_id,
        )
    except DebateNotPendingApproval as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return HumanTurnResponse(new_scorecards=scorecards)


class ApprovalRequest(BaseModel):
    approver_id: str
    approver_role: Optional[str] = None  # unenforced placeholder, Section 12
    decision: str  # "approved" | "rejected"
    note: Optional[str] = None


class ApprovalResponse(BaseModel):
    approval_id: UUID
    decision: str
    applied_ops: list[dict] = []
    export_markdown: Optional[str] = None


@router.post("/{scorecard_id}", response_model=ApprovalResponse)
async def decide(
    scorecard_id: UUID, body: ApprovalRequest, pool=Depends(get_pool)
) -> ApprovalResponse:
    """
    HONEST LIMIT on the state check added below: it reads current_state()
    without a row lock, then applies, then transitions (machine.transition()
    itself DOES take a real FOR UPDATE lock, but only at that later point).
    Two decide() calls landing at almost exactly the same instant can both
    pass the early check before either has committed a transition -- a
    narrower window than the pre-fix bug (which had no early check at
    all), not a fully serialized one. Closing that fully would mean
    KnowledgeUpdater.apply() running inside the SAME locked transaction
    DebateStateMachine.transition() uses, which needs that module's own
    connection-reuse API to extend without duplicating its transition SQL
    outside the state-machine module (CLAUDE.md: "state transitions live
    in the state-machine modules only") -- out of scope for this pass;
    flagged here rather than silently left unstated.
    """
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be 'approved' or 'rejected'")

    # A validated OIDC actor always overrides the request body's
    # self-asserted `approver_id` -- the same rule get_scope/authn.py
    # document and app/api/agent_store.py follows for its own write
    # paths. `approver_id` survives only as the fallback for the
    # unauthenticated posture. This is THE named-human-approval gate
    # (CLAUDE.md: "apply only after a named human approves"), so the
    # attribution recorded here must be the real identity when one is
    # available, not whatever the caller wrote in the request body.
    resolved_approver_id = current_actor_id() or body.approver_id

    row = await pool.fetchrow(
        "SELECT s.id, s.debate_id, s.candidate_id, s.layer1_passed, s.blast_radius, "
        "s.reversible, s.recommendation, c.summary, c.change_set, c.supporters "
        "FROM scorecards s JOIN candidates c ON c.id = s.candidate_id WHERE s.id = $1",
        scorecard_id,
    )
    if row is None:
        raise HTTPException(404, "scorecard not found")

    machine = DebateStateMachine(pool)
    updater = KnowledgeUpdater(pool)
    now = datetime.now(timezone.utc)
    applied: list[dict] = []
    export_md: Optional[str] = None

    change_set = ChangeSet(**(row["change_set"] or {"ops": []}))

    # REAL GAP CLOSED: this used to call updater.apply() unconditionally,
    # BEFORE ever checking whether the debate's own state actually
    # supports this decision -- the state-machine gate only ran AFTER the
    # write, via machine.transition() below. That meant re-deciding an
    # already-decided (or not-yet-PENDING_APPROVAL) scorecard still
    # applied the change_set and inserted a second `approvals` audit row
    # -- for change sets containing create_task_node/create_knowledge_node/
    # create_edge ops (no natural idempotency guard, unlike
    # invalidate_edge/update_*_node's t_invalid-IS-NULL checks), this
    # silently duplicated real graph content -- and ONLY THEN failed with
    # a 409 from the transition check, after the damage was already done.
    # This is exactly the "gate and writer together" half-gate CLAUDE.md's
    # own hard rule 6 names: the enforcement trigger (the state check) must
    # land with its writer, not after it. Checking current_state() here,
    # before any write, closes the deterministic case (a retried/replayed/
    # already-decided decide() call) -- see this function's own docstring
    # note below for the narrower race window this does not close.
    to_state = "APPROVED" if body.decision == "approved" else "REJECTED"
    current_state = await machine.current_state(row["debate_id"])
    try:
        assert_transition(current_state, to_state)
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc

    if body.decision == "approved":
        try:
            applied = await updater.apply(change_set, resolved_approver_id, at=now)
        except ChangeApplicationError as exc:
            # Leave the debate in PENDING_APPROVAL so it can be retried or
            # re-debated once the conflict is understood.
            log.error("change application failed for scorecard %s: %s", scorecard_id, exc)
            raise HTTPException(409, f"could not apply change: {exc}") from exc

    approval_row = await pool.fetchrow(
        "INSERT INTO approvals (scorecard_id, candidate_id, approver_id, approver_role, "
        "decision, note, decided_at, applied_at, applied_ops) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
        scorecard_id, row["candidate_id"], resolved_approver_id, body.approver_role,
        body.decision, body.note, now, now if applied else None, applied,
    )

    try:
        await machine.transition(
            row["debate_id"],
            "APPROVED" if body.decision == "approved" else "REJECTED",
            reason=body.note or f"{body.decision} by {resolved_approver_id}",
            actor=resolved_approver_id,
        )
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc

    if body.decision == "approved":
        scorecard = Scorecard(
            id=row["id"],
            debate_id=row["debate_id"],
            candidate_id=row["candidate_id"],
            summary=row["summary"],
            proposers=row["supporters"] or [],
            layer1=Layer1Result(candidate_id=row["candidate_id"], passed=row["layer1_passed"]),
            blast_radius=row["blast_radius"],
            reversible=row["reversible"],
            recommendation=row["recommendation"],
        )
        export_md = render_export(
            scorecard, change_set, resolved_approver_id, now.isoformat()
        )

    return ApprovalResponse(
        approval_id=approval_row["id"],
        decision=body.decision,
        applied_ops=applied,
        export_markdown=export_md,
    )


@router.get("/pending")
async def list_pending(pool=Depends(get_pool)):
    """Scorecards awaiting a decision, newest first. Summary view only —
    use GET /{scorecard_id} for the full detail a real approval screen needs."""
    rows = await pool.fetch(
        "SELECT s.id, s.debate_id, s.candidate_id, s.layer1_passed, s.groundedness_score, "
        "s.blast_radius, s.reversible, s.recommendation, c.summary, c.supporters, s.created_at "
        "FROM scorecards s "
        "JOIN candidates c ON c.id = s.candidate_id "
        "JOIN debates d ON d.id = s.debate_id "
        "WHERE d.state = 'PENDING_APPROVAL' "
        "ORDER BY s.created_at DESC"
    )
    return [dict(r) for r in rows]


@router.get("/{scorecard_id}")
async def get_detail(scorecard_id: UUID, pool=Depends(get_pool)):
    """
    Full scorecard detail: Layer 1 reasoning, the proposed change set, and
    the debate transcript that produced it. This is what a real approval
    screen renders — the summary list alone isn't enough to approve
    responsibly against.
    """
    row = await pool.fetchrow(
        "SELECT s.*, c.summary, c.rationale, c.change_set, c.supporters, "
        "d.round_number, d.termination_reason, t.task_node_id "
        "FROM scorecards s "
        "JOIN candidates c ON c.id = s.candidate_id "
        "JOIN debates d ON d.id = s.debate_id "
        "JOIN triggers t ON t.debate_id = d.id "
        "WHERE s.id = $1",
        scorecard_id,
    )
    if row is None:
        raise HTTPException(404, "scorecard not found")

    turns = await pool.fetch(
        "SELECT round_number, speaker_id, speaker_kind, model_used, action, "
        "candidate_id, content, cites, created_at "
        "FROM debate_turns WHERE debate_id = $1 ORDER BY round_number, created_at",
        row["debate_id"],
    )

    detail = dict(row)
    detail["transcript"] = [dict(t) for t in turns]
    return detail
