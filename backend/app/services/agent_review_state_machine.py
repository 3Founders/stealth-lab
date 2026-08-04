"""
Agent review lifecycle state machine (AGENT_STORE_PLAN.md, Section 3).

Deliberately mirrors app/debate/state_machine.py's structure rather than
sharing code with it: same discipline (one explicit transition table,
row-locked transitions, an immutable event log), applied to a genuinely
different entity with a different lifecycle. Forcing agents through the
debates table to reuse the class literally would be the wrong kind of
reuse -- an agent under review is not a debate.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import asyncpg

from app.models.agent import AgentReviewState

# to_state -> set of legal from_states.
TRANSITIONS: dict[AgentReviewState, frozenset[AgentReviewState]] = {
    "ingested": frozenset(),
    "under_review": frozenset({"ingested"}),
    "pending_human_approval": frozenset({"under_review"}),
    "approved": frozenset({"pending_human_approval"}),
    # rejected is reachable from any pre-decision state: automated
    # review can reject outright (Layer 1 failure), or a human can
    # reject after seeing it -- both are the same terminal state.
    "rejected": frozenset({"ingested", "under_review", "pending_human_approval"}),
}

TERMINAL: frozenset[AgentReviewState] = frozenset({"approved", "rejected"})


class IllegalTransition(Exception):
    pass


def can_transition(from_state: AgentReviewState, to_state: AgentReviewState) -> bool:
    return from_state in TRANSITIONS.get(to_state, frozenset())


def assert_transition(from_state: AgentReviewState, to_state: AgentReviewState) -> None:
    if not can_transition(from_state, to_state):
        raise IllegalTransition(
            f"cannot move agent review from {from_state} to {to_state}; "
            f"legal predecessors of {to_state} are "
            f"{sorted(TRANSITIONS.get(to_state, frozenset())) or '(none -- initial state)'}"
        )


class AgentReviewStateMachine:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def current_state(self, agent_id: UUID) -> AgentReviewState:
        row = await self._pool.fetchrow(
            "SELECT review_state::text AS state FROM agents WHERE id = $1", agent_id
        )
        if row is None:
            raise LookupError(f"no agent {agent_id}")
        return row["state"]

    async def transition(
        self,
        agent_id: UUID,
        to_state: AgentReviewState,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> AgentReviewState:
        """
        Move an agent's review_state to `to_state`, recording an
        immutable event. Runs in one transaction with a row lock so two
        concurrent reviewers (or a retry racing a first attempt) can't
        both read the same state and both advance it.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT review_state::text AS state FROM agents WHERE id = $1 FOR UPDATE",
                    agent_id,
                )
                if row is None:
                    raise LookupError(f"no agent {agent_id}")
                from_state: AgentReviewState = row["state"]
                assert_transition(from_state, to_state)

                await conn.execute(
                    "UPDATE agents SET review_state = $2::agent_review_state "
                    "WHERE id = $1",
                    agent_id, to_state,
                )
                await conn.execute(
                    "INSERT INTO agent_review_events "
                    "(agent_id, from_state, to_state, reason, actor) "
                    "VALUES ($1, $2::agent_review_state, $3::agent_review_state, $4, $5)",
                    agent_id, from_state, to_state, reason, actor,
                )
        return to_state

    async def history(self, agent_id: UUID) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT from_state::text AS from_state, to_state::text AS to_state, "
            "reason, actor, occurred_at FROM agent_review_events "
            "WHERE agent_id = $1 ORDER BY occurred_at, id",
            agent_id,
        )
        return [dict(r) for r in rows]
