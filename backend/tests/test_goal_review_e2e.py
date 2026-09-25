"""Reviewer queue for the Goal hierarchy, against a real database.

Proposed edges are listed with both Goals (never when an endpoint is private to
someone else); a reviewer can accept/reject ONLY a proposed edge, through the same
graph checks (a forged or cyclic acceptance is refused); placement's orphan /
uncertain flags become idempotent queue items."""
from __future__ import annotations

import pytest

from app.services import goal_review as review
from app.services.access import AccessScope
from app.services.goal_abstraction import GoalRelationCycleError, GoalRelationStatusConflict
from app.services.ingestion_jobs import handle_goal_abstraction_audit
from tests.test_goal_abstraction_e2e import DATABASE_URL, _goal, _levels, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
ANON = AccessScope.anonymous()


async def _propose(pool, specific: str, abstract: str) -> None:
    await pool.execute(
        "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, confidence, provenance) "
        "VALUES ($1::uuid, $2::uuid, 'SPECIALIZES', 'proposed', 0.55, 'identity_resolution')", specific, abstract)


@pytest.mark.asyncio
async def test_reviewer_accepts_and_rejects_only_proposed_edges(pool):
    run = _run_id()
    parent = await _goal(pool, f"e2e {run} review parent")
    child = await _goal(pool, f"e2e {run} review child")
    secret = await _goal(pool, f"e2e {run} review secret child", visibility="private", owner_id=f"owner-{run}")
    await _propose(pool, child, parent)
    await _propose(pool, secret, parent)

    listed, _ = await review.list_proposed_relations(pool, access_scope=ANON, limit=200)
    pairs = {(item["specific_goal"]["id"], item["abstract_goal"]["id"]) for item in listed}
    assert (child, parent) in pairs
    assert (secret, parent) not in pairs                          # a private endpoint never enters another's queue

    # a relation that was never proposed cannot be "accepted" into existence
    with pytest.raises(GoalRelationStatusConflict):
        await review.decide_proposed_relation(
            pool, specific_goal_id=parent, abstract_goal_id=child, decision="accept",
            reviewer="reviewer-1", reason="forged", access_scope=ANON)

    accepted = await review.decide_proposed_relation(
        pool, specific_goal_id=child, abstract_goal_id=parent, decision="accept",
        reviewer="reviewer-1", reason="child is a specific case of parent", access_scope=ANON)
    assert accepted["status"] == "accepted"
    assert (await _levels(pool, child))[child]["abstraction_level"] == 1

    # deciding it again is a conflict (it is no longer proposed), not a silent overwrite
    with pytest.raises(GoalRelationStatusConflict):
        await review.decide_proposed_relation(
            pool, specific_goal_id=child, abstract_goal_id=parent, decision="reject",
            reviewer="reviewer-2", reason="changed my mind", access_scope=ANON)

    # accepting the reverse edge would close a cycle: refused by the graph checks
    await _propose(pool, parent, child)
    with pytest.raises(GoalRelationCycleError):
        await review.decide_proposed_relation(
            pool, specific_goal_id=parent, abstract_goal_id=child, decision="accept",
            reviewer="reviewer-1", reason="cycle attempt", access_scope=ANON)
    rejected = await review.decide_proposed_relation(
        pool, specific_goal_id=parent, abstract_goal_id=child, decision="reject",
        reviewer="reviewer-1", reason="inverse of an accepted edge", access_scope=ANON)
    assert rejected["status"] == "rejected"


@pytest.mark.asyncio
async def test_placement_flags_become_idempotent_queue_items(pool):
    run = _run_id()
    orphan = await _goal(pool, f"e2e {run} lonely goal")

    first = await handle_goal_abstraction_audit(pool, {"goal_id": orphan, "reason": "orphan"})
    again = await handle_goal_abstraction_audit(pool, {"goal_id": orphan, "reason": "orphan"})
    assert first["recorded"] is True and again["recorded"] is False

    items, _ = await review.list_review_items(pool, access_scope=ANON, limit=200)
    mine = [item for item in items if item["goal_id"] == orphan]
    assert len(mine) == 1 and mine[0]["reason"] == "orphan"

    assert await review.close_review_item(pool, item_id=mine[0]["id"], reviewer="reviewer-1", status="dismissed")
    assert not await review.close_review_item(pool, item_id=mine[0]["id"], reviewer="reviewer-1", status="resolved")
    open_items, _ = await review.list_review_items(pool, access_scope=ANON, limit=200)
    assert orphan not in {item["goal_id"] for item in open_items}
