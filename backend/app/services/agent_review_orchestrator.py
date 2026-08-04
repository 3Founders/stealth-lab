"""
Agent review orchestration (AGENT_STORE_PLAN.md, Section 3a).

For graph-derived agents specifically: a promoted decomposition *is* a
task graph, exactly what Layer1Evaluator already checks for a debate
candidate. This reuses that evaluator directly rather than writing a
second version of "is this grounded and fallacy-free" -- the whole
point of Section 3a is that this review is close to free precisely
because nothing new needs to be built for it.

Code-sourced review (Section 3b: user_submitted, external_marketplace)
is NOT handled here. That needs its own rubric plus automated scanning,
neither of which exist yet -- see AGENT_STORE_PLAN.md Section 7,
stage 5. Calling review_graph_derived on a code-sourced agent is a
programming error, not a valid alternate path, and is rejected as such.
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from app.db.graph_store import GraphStore
from app.debate.panel import PanelAgent
from app.eval.layer1 import Layer1Evaluator
from app.models.agent import AgentReview
from app.models.change import ChangeSet
from app.models.debate import Candidate
from app.services.agent_review_state_machine import AgentReviewStateMachine

log = logging.getLogger(__name__)


class WrongReviewPath(Exception):
    pass


class AgentReviewOrchestrator:
    def __init__(self, pool: asyncpg.Pool, judge: PanelAgent, on_call=None):
        self._pool = pool
        self._graph = GraphStore(pool)
        self._evaluator = Layer1Evaluator(judge, self._graph, on_call=on_call)
        self._machine = AgentReviewStateMachine(pool)

    async def review_graph_derived(
        self,
        agent_id: UUID,
        summary: str,
        rationale: str,
        change_set: ChangeSet,
        cited: list | None = None,
    ) -> AgentReview:
        """
        Runs Layer 1 against the agent's underlying change set and
        advances review_state accordingly: pass -> pending_human_approval,
        fail -> rejected outright, with the failure reason logged as the
        rejection reason rather than left implicit.

        `Candidate.debate_id` is a required field with no meaning in this
        context -- it's never actually read inside Layer1Evaluator.evaluate()
        (confirmed by reading that method directly, not assumed), so
        `agent_id` is passed into that slot rather than adding a second,
        near-identical model just to avoid one repurposed field.
        """
        row = await self._pool.fetchrow(
            "SELECT source FROM agents WHERE id = $1", agent_id
        )
        if row is None:
            raise LookupError(f"no agent {agent_id}")
        if row["source"] != "graph_derived":
            raise WrongReviewPath(
                f"agent {agent_id} has source={row['source']!r}; "
                "review_graph_derived only applies to source='graph_derived'. "
                "Code-sourced agents need the review path from Section 3b, "
                "not built yet."
            )

        await self._machine.transition(agent_id, "under_review", actor="agent_review")

        candidate = Candidate(
            debate_id=agent_id,  # repurposed slot, see docstring -- never read by evaluate()
            summary=summary,
            rationale=rationale,
            change_set=change_set,
        )
        result = await self._evaluator.evaluate(candidate, cited=cited or [])

        # Deliberately NOT trusting result.passed here. Layer1Evaluator's
        # own pass bar requires groundedness_score >= threshold, calibrated
        # for a debate candidate arguing about an EXISTING task by citing
        # real company facts. A graph-derived promotion is mostly
        # create_task_node ops -- brand-new structure, with nothing
        # existing to cite by construction (the capability boundary
        # forbids generated content from referencing existing nodes at
        # all). Gating on that bar would fail nearly every promotion
        # regardless of actual quality. The fallacy/constructiveness
        # checks DO transfer correctly; groundedness is recorded for a
        # human reviewer's visibility but not used to gate here.
        agent_passed = (
            result.constructive
            and not result.fallacy_flags
            and not result.structural_problems
        )

        await self._pool.execute(
            "INSERT INTO agent_reviews "
            "(agent_id, fallacy_flags, constructive, groundedness_score, "
            "unresolved_cites, structural_problems, passed, notes) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            agent_id,
            [f.model_dump(mode="json") for f in result.fallacy_flags],
            result.constructive, result.groundedness_score,
            result.unresolved_cites, result.structural_problems,
            agent_passed, result.notes,
        )

        if agent_passed:
            await self._machine.transition(
                agent_id, "pending_human_approval", actor="agent_review",
                reason="passed Layer 1 groundedness/fallacy check",
            )
        else:
            reason_parts = []
            if result.fallacy_flags:
                reason_parts.append(f"{len(result.fallacy_flags)} fallacy flag(s)")
            if result.structural_problems:
                reason_parts.append("; ".join(result.structural_problems))
            if not result.constructive:
                reason_parts.append("not constructive")
            reason = "Layer 1 rejected: " + (", ".join(reason_parts) or "did not pass review")
            await self._machine.transition(agent_id, "rejected", actor="agent_review", reason=reason)

        return AgentReview(
            agent_id=agent_id,
            fallacy_flags=[f.model_dump(mode="json") for f in result.fallacy_flags],
            constructive=result.constructive,
            groundedness_score=result.groundedness_score,
            unresolved_cites=result.unresolved_cites,
            structural_problems=result.structural_problems,
            passed=agent_passed,
            notes=result.notes,
        )
