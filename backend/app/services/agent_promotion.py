"""
Promoting an approved decomposition into a reusable agent
(AGENT_STORE_PLAN.md, Section 4: graph-derived).

Promotion is a deliberate, manual action for now, not automatic by
usage count. No decomposition has real reuse data yet to threshold
against; inventing a number now would be a guess dressed up as a rule.
See AGENT_STORE_PLAN.md's "open decisions" for the honest version of
this.

Promotion always triggers a fresh Layer 1 review, even though the
source decomposition was already approved once. "Safe for this one
input" and "safe to reuse generally" are different claims -- a
decomposition that fit one person's exact wording well can still fail
to generalize, and nothing else in this project gets a review bypass.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg

from app.debate.panel import PanelAgent
from app.models.change import ChangeSet
from app.services.agent_review_orchestrator import AgentReviewOrchestrator

log = logging.getLogger(__name__)


class DecompositionNotApproved(Exception):
    pass


class DecompositionNotFound(Exception):
    pass


async def promote_decomposition(
    pool: asyncpg.Pool,
    decomposition_id: UUID,
    judge: PanelAgent,
    actor: Optional[str] = None,
) -> dict:
    """
    Reads an approved decomposition, creates a new `agents` row from it
    (execution_mode='graph_workflow', pointing at the task nodes the
    decomposition actually created via its stored `applied_refs`), and
    immediately runs it through Layer 1 review.

    Returns {"agent_id": ..., "review": AgentReview}. The agent may come
    back rejected -- that's a correct, informative outcome, not a
    failure of this function.
    """
    row = await pool.fetchrow(
        "SELECT problem, reasoning, change_set, applied_refs, status "
        "FROM decompositions WHERE id = $1",
        decomposition_id,
    )
    if row is None:
        raise DecompositionNotFound(f"no decomposition {decomposition_id}")
    if row["status"] != "approved":
        raise DecompositionNotApproved(
            f"decomposition {decomposition_id} has status={row['status']!r}; "
            "only an approved decomposition can be promoted."
        )
    if not row["applied_refs"]:
        # Defensive: an approved decomposition should always have
        # applied_refs from the apply step, but a promotion built on top
        # of nothing applied would silently point at nothing runnable.
        raise DecompositionNotApproved(
            f"decomposition {decomposition_id} is approved but has no "
            "applied_refs -- nothing was actually written to the graph "
            "to promote."
        )

    task_ids = [UUID(v) for v in row["applied_refs"].values()]
    change_set = ChangeSet(**row["change_set"])

    # Name/description derived from the decomposition's own problem
    # statement and reasoning, not reinvented -- this is what a human
    # reviewer will actually read to decide whether to approve it.
    name = (row["problem"][:80] + "...") if len(row["problem"]) > 80 else row["problem"]
    description = row["reasoning"] or row["problem"]

    agent_row = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, source_decomposition_id, "
        "execution_mode, workflow_task_ids, created_by) "
        "VALUES ($1, $2, 'graph_derived', $3, 'graph_workflow', $4, $5) "
        "RETURNING id",
        name, description, decomposition_id,
        [str(t) for t in task_ids], actor,
    )
    agent_id = agent_row["id"]

    # Same graceful-degradation pattern as Onboarder._embed_seeded():
    # outside the insert's own transaction, a real embedding-provider
    # outage must not roll back an otherwise-successful promotion. The
    # agent is still correct and usable, just not vector-searchable
    # until backfilled -- logged, not silently dropped.
    try:
        from app.services.embeddings import Embedder, node_text, to_pgvector

        embedder = Embedder()
        vector = await embedder.embed_one(node_text(name, description), input_type="document")
        await pool.execute(
            "UPDATE agents SET embedding = $2::vector WHERE id = $1",
            agent_id, to_pgvector(vector),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("embedding failed for promoted agent %s: %s", agent_id, exc)

    orchestrator = AgentReviewOrchestrator(pool, judge)
    review = await orchestrator.review_graph_derived(
        agent_id, summary=name, rationale=description, change_set=change_set,
    )

    log.info(
        "promoted decomposition %s to agent %s (passed=%s)",
        decomposition_id, agent_id, review.passed,
    )
    return {"agent_id": agent_id, "review": review}
