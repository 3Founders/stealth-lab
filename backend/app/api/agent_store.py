"""
Agent Store review endpoints (AGENT_STORE_PLAN.md, stage 2).

Thin wrappers over app/services/agent_promotion.py and
app/services/agent_decision.py -- the real logic and its verification
live there and in integration_check_v2_agent_promotion.py; these
endpoints exist so that logic is reachable over HTTP at all.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_scope
from app.debate.panel import default_judge, default_panel
from app.services.access import AccessScope
from app.services.agent_decision import AgentNotPendingApproval, decide_agent
from app.services.agent_promotion import (
    DecompositionNotApproved,
    DecompositionNotFound,
    promote_decomposition,
)
from app.services.agent_search import search_agents
from app.services.agent_review_state_machine import AgentReviewStateMachine
from app.services.code_review import CodeSourcedReviewOrchestrator
from app.services.execution import default_registry

router = APIRouter(prefix="/v1/agent-store", tags=["agent-store"])


async def get_pool(request: Request):
    return request.app.state.pool


class PromoteRequest(BaseModel):
    decomposition_id: UUID
    actor: Optional[str] = None


class PromoteResponse(BaseModel):
    agent_id: UUID
    review_state: str
    passed_review: bool
    review_notes: str


class DecideRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    actor: Optional[str] = None
    reason: Optional[str] = None
    acknowledge_sandbox_limitations: bool = False


class DecideResponse(BaseModel):
    agent_id: UUID
    review_state: str
    runnable: bool


class AgentSearchResultOut(BaseModel):
    id: UUID
    name: str
    description: str
    source: str
    execution_mode: str
    runnable: bool


class SearchResponse(BaseModel):
    results: list[AgentSearchResultOut]


@router.get("", response_model=SearchResponse)
async def browse_or_search(
    q: Optional[str] = None,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> SearchResponse:
    """No `q` -- browse listing, newest first. With `q` -- ranked search."""
    results = await search_agents(pool, query=q, scope=scope)
    return SearchResponse(results=[
        AgentSearchResultOut(
            id=r.id, name=r.name, description=r.description,
            source=r.source, execution_mode=r.execution_mode, runnable=r.runnable,
        )
        for r in results
    ])


@router.get("/pending")
async def list_pending(pool=Depends(get_pool)):
    """Agents awaiting a human decision, newest first."""
    rows = await pool.fetch(
        "SELECT id, name, description, source::text AS source, "
        "execution_mode::text AS execution_mode, source_decomposition_id, t_created "
        "FROM agents WHERE review_state = 'pending_human_approval' AND t_invalid IS NULL "
        "ORDER BY t_created DESC LIMIT 100"
    )
    return [dict(r) for r in rows]


class SubmitAgentRequest(BaseModel):
    name: str
    description: str
    source: str = Field(pattern="^(user_submitted|external_marketplace)$")
    # user_submitted: {"requested_input": ..., "requested_output": ..., "category": ...}
    # -- a structured request, never raw code, per AGENT_STORE_PLAN.md
    # Section 4's deliberately narrow scope for this source.
    # external_marketplace: {"repo_url": ..., "code": "..."} -- code triggers
    # a real bandit scan in the review that follows.
    source_detail: dict = {}
    submitted_by: Optional[str] = None


class SubmitAgentResponse(BaseModel):
    agent_id: UUID
    review_state: str
    passed_review: bool
    reviewer_notes: str


@router.post("/submit", response_model=SubmitAgentResponse)
async def submit_agent(body: SubmitAgentRequest, pool=Depends(get_pool)) -> SubmitAgentResponse:
    """
    Registers a new code-sourced agent and immediately runs it through
    independent review (app/services/code_review.py) -- the same
    "review happens before a human ever sees this" discipline every
    other content path in this project follows.

    Never sets runnable here, regardless of review outcome -- see
    agent_decision.py's _compute_runnable: that requires an explicit
    human acknowledgment of the sandbox's real, stated limitations
    (app/services/sandbox.py), made at decision time, not submission
    time.
    """
    row = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, "
        "skill_ref, source_detail, created_by) "
        "VALUES ($1, $2, $3, 'local_skill', 'pending_review', $4, $5) RETURNING id",
        body.name, body.description, body.source, body.source_detail, body.submitted_by,
    )
    agent_id = row["id"]

    panel = default_panel()
    if len(panel) < 2:
        raise HTTPException(
            503, "at least two independent reviewers are required for code-sourced "
            "review, and fewer than two are currently configured"
        )
    orchestrator = CodeSourcedReviewOrchestrator(pool, panel[:2])
    result = await orchestrator.review_code_sourced(agent_id)

    machine = AgentReviewStateMachine(pool)
    state = await machine.current_state(agent_id)
    notes = "; ".join(o["notes"] for o in result["opinions"] if o["notes"])
    return SubmitAgentResponse(
        agent_id=agent_id, review_state=state, passed_review=result["passed"],
        reviewer_notes=notes,
    )



@router.post("/promote", response_model=PromoteResponse)
async def promote(body: PromoteRequest, pool=Depends(get_pool)) -> PromoteResponse:
    try:
        outcome = await promote_decomposition(
            pool, body.decomposition_id, default_judge(), actor=body.actor,
        )
    except DecompositionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DecompositionNotApproved as exc:
        raise HTTPException(409, str(exc)) from exc

    machine = AgentReviewStateMachine(pool)
    state = await machine.current_state(outcome["agent_id"])
    return PromoteResponse(
        agent_id=outcome["agent_id"], review_state=state,
        passed_review=outcome["review"].passed, review_notes=outcome["review"].notes,
    )


@router.post("/{agent_id}/decide", response_model=DecideResponse)
async def decide(agent_id: UUID, body: DecideRequest, pool=Depends(get_pool)) -> DecideResponse:
    try:
        result = await decide_agent(
            pool, agent_id, body.decision, default_registry(),
            actor=body.actor, reason=body.reason,
            acknowledge_sandbox_limitations=body.acknowledge_sandbox_limitations,
        )
    except AgentNotPendingApproval as exc:
        raise HTTPException(409, str(exc)) from exc

    return DecideResponse(
        agent_id=result["agent_id"], review_state=result["review_state"],
        runnable=result["runnable"],
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, pool=Depends(get_pool)):
    row = await pool.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
    if row is None:
        raise HTTPException(404, "agent not found")
    return dict(row)
