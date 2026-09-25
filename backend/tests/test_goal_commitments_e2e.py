"""Credit commitments -- escrow bounty (migration 119), against a real database.

Commit locks Credits (balance can't go negative, retries are idempotent), the
committer can withdraw before resolution, and when the Goal is resolved by the
EXISTING verification rule the open commitments are paid to the contributor of the
verifying Procedure (released on self-dealing). Demand is quadratic, aggregated
over accepted descendants once per commitment (diamonds), and private descendants
contribute nothing to a public viewer. Credits never resolve anything."""
from __future__ import annotations

import uuid

import pytest

from app.economy import commitments as cm
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import persist_goal_relation
from app.services.procedures import capture_procedure, record_execution_outcome
from tests.test_goal_abstraction_e2e import DATABASE_URL, _goal, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
ANON = AccessScope.anonymous()


async def _grant(pool, who: str, credits: int) -> None:
    await pool.execute(
        "INSERT INTO credit_ledger_events (contributor_id, amount, reason, metadata, created_by) "
        "VALUES ($1, $2, 'admin_adjustment', '{}'::jsonb, 'e2e')", who, credits)


async def _balance(pool, who: str) -> float:
    return float(await pool.fetchval(
        "SELECT COALESCE(SUM(amount), 0) FROM credit_ledger_events WHERE contributor_id = $1", who))


async def _edge(pool, specific: str, abstract: str) -> None:
    await persist_goal_relation(
        pool, specific, abstract, status="accepted", provenance="e2e", access_scope=AccessScope.unrestricted(),
        tenant_scope=TenantScope.commons(), decided_by="e2e", decision_metadata={"reason": "e2e"})


@pytest.mark.asyncio
async def test_escrow_bounty_lifecycle(pool):
    run = _run_id()
    alice, bob, solver = f"alice-{run}", f"bob-{run}", f"solver-{run}"
    for who in (alice, bob, solver):
        await _grant(pool, who, 100)

    proc = await capture_procedure(
        pool, name=f"e2e {run} bounty procedure", goal=f"e2e {run} goal with a bounty", steps=[{"description": "x"}],
        provenance="prior_library", scope_type="global")
    goal_id = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id = $1::uuid", proc["id"])
    await pool.execute(
        "INSERT INTO procedure_submissions (goal_id, submission_type, name, submitted_by, procedure_row_id, status, "
        "reviewed_by, reviewed_at, visibility, scope_type) "
        "VALUES ($1::uuid, 'new', $2, $3, $4::uuid, 'accepted', 'reviewer', now(), 'public', 'global')",
        goal_id, f"e2e {run} bounty procedure", solver, proc["id"])
    from app.services import search_projection as sp
    await sp.drain_outbox(pool)

    # --- commit: locks Credits, idempotent, never below zero
    first = await cm.commit(pool, goal_id=goal_id, contributor_id=alice, credits=25, idempotency_key="k1", access_scope=ANON)
    again = await cm.commit(pool, goal_id=goal_id, contributor_id=alice, credits=25, idempotency_key="k1", access_scope=ANON)
    assert first["created"] and not again["created"] and again["id"] == first["id"]
    assert await _balance(pool, alice) == 75
    with pytest.raises(cm.InsufficientCredits):
        await cm.commit(pool, goal_id=goal_id, contributor_id=alice, credits=76, idempotency_key="k2", access_scope=ANON)

    # --- withdraw before resolution: back to the committer, exactly once
    bobs = await cm.commit(pool, goal_id=goal_id, contributor_id=bob, credits=16, idempotency_key="b1", access_scope=ANON)
    assert (await cm.withdraw(pool, commitment_id=bobs["id"], contributor_id=bob))["released"] == 16
    assert await _balance(pool, bob) == 100
    with pytest.raises(cm.CommitmentError):
        await cm.withdraw(pool, commitment_id=bobs["id"], contributor_id=bob)
    with pytest.raises(cm.CommitmentError):     # someone else's commitment is not yours to withdraw
        await cm.withdraw(pool, commitment_id=first["id"], contributor_id=bob)
    await cm.commit(pool, goal_id=goal_id, contributor_id=bob, credits=9, idempotency_key="b2", access_scope=ANON)
    await cm.commit(pool, goal_id=goal_id, contributor_id=solver, credits=4, idempotency_key="s1", access_scope=ANON)

    demand = (await cm.goal_demand(pool, [goal_id], access_scope=ANON))[goal_id]
    assert demand["direct"] == {"committed_credits": 38.0, "supporters": 3, "demand_score": 10.0}   # sqrt 25+9+4

    # --- Credits never resolve: the Goal is still unresolved with 38 Credits on it
    assert await pool.fetchval("SELECT resolved_at FROM goals WHERE id = $1::uuid", goal_id) is None

    # --- resolution by the EXISTING rule (>=10 successes, 0 failures, >=3 contexts) pays out
    for index in range(10):
        await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key=f"c{index % 3}")
    assert await pool.fetchval("SELECT resolved_at FROM goals WHERE id = $1::uuid", goal_id) is not None
    settled = {row["contributor_id"]: row for row in await pool.fetch(
        "SELECT contributor_id, settlement, settled_to FROM goal_commitments WHERE goal_id = $1::uuid "
        "AND settlement IS NOT NULL AND id <> $2::uuid", goal_id, bobs["id"])}
    assert settled[alice]["settlement"] == "bounty_payout" and settled[alice]["settled_to"] == solver
    assert settled[bob]["settlement"] == "bounty_payout"
    assert settled[solver]["settlement"] == "goal_commitment_release"      # no paying yourself
    assert await _balance(pool, solver) == 100 + 25 + 9                     # own 4 locked then released
    assert await _balance(pool, alice) == 75 and await _balance(pool, bob) == 91
    assert (await cm.settle_goal_bounties(pool, goal_id, procedure_row_id=proc["id"]))["paid"] == 0   # idempotent

    with pytest.raises(cm.GoalNotCommittable):
        await cm.commit(pool, goal_id=goal_id, contributor_id=bob, credits=1, idempotency_key="late", access_scope=ANON)

    # --- the ledger stays append-only
    with pytest.raises(Exception, match="append-only"):
        await pool.execute("UPDATE credit_ledger_events SET amount = 0 WHERE id = $1::uuid", first["id"])


@pytest.mark.asyncio
async def test_demand_aggregates_once_per_ancestor_and_hides_private_descendants(pool):
    run = _run_id()
    root = await _goal(pool, f"e2e {run} demand root")
    left = await _goal(pool, f"e2e {run} demand left")
    right = await _goal(pool, f"e2e {run} demand right")
    leaf = await _goal(pool, f"e2e {run} demand leaf")
    hidden = await _goal(pool, f"e2e {run} demand private leaf", visibility="private", owner_id=f"carol-{run}")
    for specific, abstract in ((left, root), (right, root), (leaf, left), (leaf, right)):
        await _edge(pool, specific, abstract)
    await _edge(pool, hidden, root)   # a private specialisation under a public root
    dave, carol = f"dave-{run}", f"carol-{run}"
    await _grant(pool, dave, 50)
    await _grant(pool, carol, 50)
    await cm.commit(pool, goal_id=leaf, contributor_id=dave, credits=16, idempotency_key="d1", access_scope=ANON)
    await cm.commit(pool, goal_id=hidden, contributor_id=carol, credits=9, idempotency_key="c1",
                    access_scope=AccessScope.for_user(carol))

    demand = await cm.goal_demand(pool, [root, left, leaf], access_scope=ANON)
    # the diamond leaf -> left/right -> root counts dave's commitment ONCE on root
    assert demand[root]["aggregated"] == {"committed_credits": 16.0, "supporters": 1, "demand_score": 4.0}
    assert demand[root]["direct"]["supporters"] == 0
    assert demand[left]["aggregated"]["committed_credits"] == 16.0
    # carol's private Goal is invisible to a public viewer: it adds nothing to the public root
    carol_view = await cm.goal_demand(pool, [root], access_scope=AccessScope.for_user(carol))
    assert carol_view[root]["aggregated"]["committed_credits"] == 25.0
