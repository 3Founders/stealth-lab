"""Goal list ranking is global and demand only ever raises a Goal, against a real DB.

* /goals: a backed Goal ranks above unbacked ones even when it is OLD (it used to be
  ranked only inside the newest page, so it could never reach page 1), and more
  demand ranks higher than less.
* roots view: a root whose more-specific Goal is backed comes before roots that
  merely have more children.
* pagination stays consistent: walking the pages yields every Goal once."""
from __future__ import annotations

import pytest

from app.economy import commitments as cm
from app.services import product_model as pm
from app.services.access import AccessScope
from tests.test_goal_abstraction_e2e import DATABASE_URL, _edge, _goal, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
ANON = AccessScope.anonymous()


async def _grant(pool, who: str, credits: int) -> None:
    await pool.execute(
        "INSERT INTO credit_ledger_events (contributor_id, amount, reason, metadata, created_by) "
        "VALUES ($1, $2, 'admin_adjustment', '{}'::jsonb, 'e2e')", who, credits)


async def _age(pool, goal_id: str, days: int) -> None:
    await pool.execute("UPDATE goal_search_index SET t_created = now() - make_interval(days => $2) "
                       "WHERE goal_id = $1::uuid", goal_id, days)


async def _all_pages(fetch, page_size=7):
    out, offset = [], 0
    while True:
        rows, more = await fetch(offset, page_size)
        out.extend(r["id"] for r in rows)
        if not more:
            return out
        offset += page_size


@pytest.mark.asyncio
async def test_list_is_ranked_globally_and_demand_only_raises(pool):
    run = _run_id()
    old_big = await _goal(pool, f"e2e {run} old goal with big demand")
    old_small = await _goal(pool, f"e2e {run} old goal with small demand")
    newest_plain = await _goal(pool, f"e2e {run} newest goal nobody backed")
    await _age(pool, old_big, 900)
    await _age(pool, old_small, 800)
    for who, credits in ((f"a-{run}", 100), (f"b-{run}", 100), (f"c-{run}", 100)):
        await _grant(pool, who, credits)
    await cm.commit(pool, goal_id=old_big, contributor_id=f"a-{run}", credits=64, idempotency_key=f"{run}a",
                    access_scope=ANON)
    await cm.commit(pool, goal_id=old_big, contributor_id=f"b-{run}", credits=49, idempotency_key=f"{run}b",
                    access_scope=ANON)
    await cm.commit(pool, goal_id=old_small, contributor_id=f"c-{run}", credits=1, idempotency_key=f"{run}c",
                    access_scope=ANON)

    ordered = await _all_pages(lambda off, n: pm.list_goals(pool, scope=ANON, limit=n, offset=off))
    assert len(ordered) == len(set(ordered))                       # every Goal exactly once across pages
    pos = {g: i for i, g in enumerate(ordered)}
    assert pos[old_big] < pos[old_small] < pos[newest_plain]       # demand first, however old; more > less

    page1, _ = await pm.list_goals(pool, scope=ANON, limit=5)
    backed = await pool.fetchval(
        "SELECT count(DISTINCT goal_id) FROM goal_commitments WHERE settlement IS NULL")
    if backed <= 5:                                                # every backed Goal fits on page 1
        assert old_big in [g["id"] for g in page1]
    small_row = next(g for g in (await pm.list_goals(pool, scope=ANON, limit=200))[0] if g["id"] == old_small)
    demand_signal = small_row["ranking"]["signals"]["demand"]
    assert demand_signal["available"] and 0 < demand_signal["value"] <= 1   # strength, shown as a percentage

    # resolved-only lists are not reordered by (settled) demand: newest first
    resolved, _ = await pm.list_goals(pool, scope=ANON, resolved=True, limit=5)
    assert all(g["resolved_at"] is not None for g in resolved)


@pytest.mark.asyncio
async def test_roots_view_puts_demand_before_child_count(pool):
    run = _run_id()
    wide_root = await _goal(pool, f"e2e {run} wide root with many specifics")
    for i in range(3):
        child = await _goal(pool, f"e2e {run} wide root specific {i}")
        await _edge(pool, child, wide_root)
    backed_root = await _goal(pool, f"e2e {run} narrow root whose specific is backed")
    backed_child = await _goal(pool, f"e2e {run} the backed specific")
    await _edge(pool, backed_child, backed_root)
    await _age(pool, backed_root, 700)
    await _grant(pool, f"d-{run}", 50)
    await cm.commit(pool, goal_id=backed_child, contributor_id=f"d-{run}", credits=9, idempotency_key=f"{run}d",
                    access_scope=ANON)

    ordered = await _all_pages(lambda off, n: pm.list_goals_browse(pool, scope=ANON, limit=n, offset=off))
    assert len(ordered) == len(set(ordered))
    pos = {g: i for i, g in enumerate(ordered)}
    assert backed_child not in pos                                 # not a root: it sits under backed_root
    assert pos[backed_root] < pos[wide_root]                       # demand (aggregated) before child count
