"""Live-database tests for the default Goal browse view (`GET /v1/goals?view=roots`).

Real SQL against a real Postgres: the browse query joins `goal_relations` to
`goals` several times with visibility/tenant/scope predicates, exactly the shape
in which fake pools have hidden production-breaking bugs before.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import product_model as pm
from app.services import search_projection as sp
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import persist_goal_relation
from app.services.goals import find_or_create_goal

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

U = AccessScope.unrestricted()
ANON = AccessScope.anonymous()


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


async def _goal(pool, name: str, **kwargs) -> str:
    created = await find_or_create_goal(
        pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
        judge_mode="none", status="active", **kwargs,
    )
    await sp.drain_outbox(pool)
    return created["id"]


async def _edge(pool, specific: str, abstract: str, status: str = "accepted"):
    return await persist_goal_relation(
        pool, specific, abstract, status=status, provenance="e2e", access_scope=U,
        tenant_scope=TenantScope.unrestricted(), decided_by="e2e-reviewer",
        decision_metadata={"reason": "e2e"},
    )


async def _browse(pool, scope, **kwargs) -> dict[str, dict]:
    """Every entry the view returns, keyed by goal id (paged through)."""
    found: dict[str, dict] = {}
    offset = 0
    while True:
        goals, more = await pm.list_goals_browse(pool, scope=scope, limit=200, offset=offset, **kwargs)
        found.update({g["id"] if isinstance(g["id"], str) else str(g["id"]): g for g in goals})
        if not more:
            return found
        offset += 200


@pytest.mark.asyncio
async def test_roots_standalone_and_diamond_children_are_listed_correctly(pool):
    run = uuid.uuid4().hex[:8]
    root, a, b, c, lone = [
        await _goal(pool, f"e2e browse {run} {n}") for n in ("root", "alpha", "bravo", "charlie", "lone")
    ]
    await _edge(pool, a, root)
    await _edge(pool, b, root)
    await _edge(pool, c, a)  # diamond: c under a and b, both under root
    await _edge(pool, c, b)

    found = await _browse(pool, ANON)
    assert root in found and found[root]["browse_kind"] == "root"
    assert found[root]["specific_count"] == 2
    assert {s["id"] for s in found[root]["specifics"]} == {a, b}
    assert lone in found and found[lone]["browse_kind"] == "standalone"
    assert found[lone]["specific_count"] == 0 and found[lone]["specifics"] == []
    # a goal with an accepted parent is not an entry; it is reached by drilling down
    for child in (a, b, c):
        assert child not in found


@pytest.mark.asyncio
async def test_proposed_and_rejected_edges_do_not_route(pool):
    run = uuid.uuid4().hex[:8]
    abstract, specific, other = [await _goal(pool, f"e2e browse {run} {n}") for n in ("abstract", "specific", "other")]
    await _edge(pool, specific, abstract, status="proposed")
    await _edge(pool, other, abstract, status="proposed")
    await _edge(pool, other, abstract, status="rejected")

    found = await _browse(pool, ANON)
    # nothing accepted: all three are standalone entries, none is a root
    for gid in (abstract, specific, other):
        assert found[gid]["browse_kind"] == "standalone"


@pytest.mark.asyncio
async def test_private_descendant_never_leaks_into_a_public_root(pool):
    run = uuid.uuid4().hex[:8]
    owner = f"e2e-owner-{run}"
    root, pub_child = [await _goal(pool, f"e2e browse {run} {n}") for n in ("root", "public child")]
    private_child = await _goal(pool, f"e2e browse {run} secret child", visibility="private", owner_id=owner)
    await _edge(pool, pub_child, root)
    await _edge(pool, private_child, root)

    anon = await _browse(pool, ANON)
    assert anon[root]["specific_count"] == 1
    assert [s["id"] for s in anon[root]["specifics"]] == [pub_child]
    assert private_child not in anon
    rendered = repr(anon[root])
    assert "secret child" not in rendered

    owner_scope = AccessScope(viewer_id=owner, include_private=True)
    mine = await _browse(pool, owner_scope)
    assert mine[root]["specific_count"] == 2
    assert {s["id"] for s in mine[root]["specifics"]} == {pub_child, private_child}


@pytest.mark.asyncio
async def test_private_parent_does_not_hide_a_goal_from_other_viewers(pool):
    run = uuid.uuid4().hex[:8]
    owner = f"e2e-owner-{run}"
    child = await _goal(pool, f"e2e browse {run} public child")
    private_parent = await _goal(pool, f"e2e browse {run} secret parent", visibility="private", owner_id=owner)
    await _edge(pool, child, private_parent)

    anon = await _browse(pool, ANON)
    # the anonymous viewer cannot see the parent, so the child is simply a standalone entry for them
    assert child in anon and anon[child]["browse_kind"] == "standalone"
    assert private_parent not in anon

    mine = await _browse(pool, AccessScope(viewer_id=owner, include_private=True))
    assert child not in mine  # for the owner it is a specific under the private root
    assert mine[private_parent]["browse_kind"] == "root"


@pytest.mark.asyncio
async def test_resolution_is_filtered_per_goal_and_never_propagates(pool):
    run = uuid.uuid4().hex[:8]
    root, child = [await _goal(pool, f"e2e browse {run} {n}") for n in ("root", "child")]
    await _edge(pool, child, root)
    await pool.execute("UPDATE goals SET resolved_at = now() WHERE id = $1::uuid", child)

    unresolved = await _browse(pool, ANON, resolved="unresolved")
    assert root in unresolved  # the root is still unresolved: a resolved child does not resolve it
    resolved = await _browse(pool, ANON, resolved="resolved")
    assert root not in resolved and child not in resolved  # a child is not an entry, and root is unresolved
    roots_row = unresolved[root]
    assert roots_row["resolved_at"] is None
    assert [s["resolved_at"] is not None for s in roots_row["specifics"]] == [True]


@pytest.mark.asyncio
async def test_pagination_reports_has_more(pool):
    run = uuid.uuid4().hex[:8]
    for i in range(3):
        await _goal(pool, f"e2e browse {run} page {i}")
    page1, more1 = await pm.list_goals_browse(pool, scope=ANON, limit=1, offset=0)
    page2, more2 = await pm.list_goals_browse(pool, scope=ANON, limit=1, offset=1)
    assert len(page1) == 1 and len(page2) == 1 and more1 is True
    assert page1[0]["id"] != page2[0]["id"]
