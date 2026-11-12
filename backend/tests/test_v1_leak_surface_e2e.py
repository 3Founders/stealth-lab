"""Live-database leak tests for the surfaces an agent and a website visitor actually use today.

Two questions, both asked against real SQL (BLOCKERS P5):

  1. TENANT ISOLATION. One signed-in user's private rows never reach another user or an anonymous caller through
     the places the earlier suites do not look: the Procedure tier `find_ways` is built on (`resolve_goal`), a
     Goal's hierarchy (`/v1/goals/{id}` neighbours), and contributions made against a private Goal.
  2. STORED PROMPT INJECTION. Contributions now come through goal submission (submit_goal, which replaced
     submit_way on 2026-11-12): a step is out of retrieval until it passes the checks and the reviewer, and a
     reviewer that cannot answer decides nothing. Those are tests/test_goal_submission_offline.py and
     tests/test_goal_submission_e2e.py.

These sit beside tests/test_mcp_tenant_isolation_e2e.py (private discoveries, private Procedures read by id,
private Goals in search), tests/test_cross_user_isolation_e2e.py (REST), tests/test_routing_isolation_e2e.py
(the routing tools) and tests/test_goals_browse_e2e.py (the roots view); they do not repeat them.

Requires DATABASE_URL (a migrated Postgres + pgvector); skips without one, like every *_e2e.py here. Note that the
REST isolation tests identify viewers with X-Viewer-Id, which the app ignores whenever Supabase sign-in is
configured (backend/.env): run those with SUPABASE_PROJECT_URL= SUPABASE_JWT_AUDIENCE= to see them pass.
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.economy import submissions as submissions_service
from app.execution.goal_resolution import resolve_goal
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import persist_goal_relation
from app.services.goal_hierarchy_read import enrich_goal
from app.services import search_projection as sp
from app.services.goals import find_or_create_goal, get_goal
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

COMMONS = TenantScope.commons()
UNRESTRICTED = AccessScope.unrestricted()


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


class NoEmbedder:
    async def embed_one(self, *a, **k):
        raise RuntimeError("embeddings off")

    async def embed_one_with_metadata(self, *a, **k):
        raise RuntimeError("embeddings off")


def _ids() -> tuple[str, str, str]:
    run = uuid.uuid4().hex[:8]
    return run, f"leak-a-{run}", f"leak-b-{run}"


async def _goal(pool, name: str, **kwargs) -> str:
    created = await find_or_create_goal(
        pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
        judge_mode="none", status="active", **kwargs,
    )
    await sp.drain_outbox(pool)          # relations read Goals through the search projection
    return created["id"]


def _ctx(pool):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))


async def _offered(pool, goal_id: str, scope: AccessScope) -> str | None:
    """The Procedure `find_ways` would hand this viewer for the Goal (name), or None."""
    tree = await resolve_goal(pool, goal_id, context={}, scope=scope, max_depth=1)
    return (tree.procedure or {}).get("name") if tree.chosen == "procedure" else None


# ---------------------------------------------------------------------------------------------------------------
# 1. tenant isolation
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_private_procedure_on_a_public_goal_is_never_offered_to_anyone_else(pool):
    run, alice, bob = _ids()
    goal_name = f"leak {run} public goal"
    goal_id = await _goal(pool, goal_name)
    secret = f"leak {run} alice private way"
    result = await capture_procedure(
        pool, name=secret, goal=goal_name, steps=[{"order": 1, "goal": "do the private thing"}],
        provenance="system_pending_review", scope_type="global", owner_id=alice, visibility="private",
    )
    assert str(await pool.fetchval("SELECT achieves_goal_id FROM procedures WHERE id = $1::uuid",
                                   result["id"])) == goal_id, "the test must attach the way to the public Goal"

    assert await _offered(pool, goal_id, AccessScope.for_user(alice)) == secret      # positive control: the owner
    assert await _offered(pool, goal_id, AccessScope.for_user(bob)) is None          # another signed-in user
    assert await _offered(pool, goal_id, AccessScope.anonymous()) is None            # nobody at all


@pytest.mark.asyncio
async def test_a_goals_procedure_list_never_names_another_users_private_procedures(pool):
    """`GET /v1/goals/{id}` and the MCP `list_goal_procedures` read this list: a public Goal must not advertise
    a private Procedure's id, name or state to anyone but its owner."""
    run, alice, bob = _ids()
    goal_name = f"leak {run} listed goal"
    goal_id = await _goal(pool, goal_name)
    secret = f"leak {run} alice unlisted way"
    await capture_procedure(
        pool, name=secret, goal=goal_name, steps=[{"order": 1, "goal": "do the private thing"}],
        provenance="system_pending_review", scope_type="global", owner_id=alice, visibility="private",
    )

    async def names(scope):
        return {p["name"] for p in (await get_goal(pool, goal_id, scope=scope))["procedures"]}

    assert await names(AccessScope.for_user(alice)) == {secret}                       # positive control
    assert await names(AccessScope.for_user(bob)) == set()
    assert await names(AccessScope.anonymous()) == set()


@pytest.mark.asyncio
async def test_a_private_goals_hierarchy_edges_never_reach_other_users(pool):
    run, alice, bob = _ids()
    root = await _goal(pool, f"leak {run} public root")
    public_child = await _goal(pool, f"leak {run} public child")
    secret_name = f"leak {run} secret child"
    secret_child = await _goal(pool, secret_name, visibility="private", owner_id=alice)
    for child in (public_child, secret_child):
        await persist_goal_relation(
            pool, child, root, status="accepted", provenance="e2e", access_scope=UNRESTRICTED,
            tenant_scope=COMMONS, decided_by="e2e-reviewer", decision_metadata={"reason": "e2e"},
        )

    async def neighbours(scope):
        enriched = await enrich_goal(pool, {"id": root}, access_scope=scope, tenant_scope=COMMONS)
        return enriched, {g["id"] for g in (enriched or {}).get("specializes") or []}

    enriched_alice, seen_alice = await neighbours(AccessScope.for_user(alice))
    assert seen_alice == {public_child, secret_child}                                 # positive control
    for scope in (AccessScope.for_user(bob), AccessScope.anonymous()):
        enriched, seen = await neighbours(scope)
        assert seen == {public_child}
        assert secret_name not in json.dumps(enriched, default=str) and secret_child not in json.dumps(enriched, default=str)


@pytest.mark.asyncio
async def test_contributions_against_a_private_goal_are_invisible_and_refused_to_others(pool):
    run, alice, bob = _ids()
    private_goal = await _goal(pool, f"leak {run} alice private goal", visibility="private", owner_id=alice)
    body = dict(
        goal_id=private_goal, submission_type="new", steps=[{"order": 1, "goal": "run the checks"}],
        rationale="because", preconditions=[{"subject": "repo", "predicate": "has", "value": "tests"}],
        expected_outcome={"checks": "pass"}, tenant_scope=COMMONS, embedder=NoEmbedder(),
    )

    with pytest.raises(ValueError, match="not found or not visible"):
        await submissions_service.create_procedure_submission(
            pool, name=f"leak {run} bob's way", actor_subject=bob, access_scope=AccessScope.for_user(bob), **body)
    assert await pool.fetchval("SELECT count(*) FROM procedure_submissions WHERE goal_id = $1::uuid", private_goal) == 0

    mine = await submissions_service.create_procedure_submission(
        pool, name=f"leak {run} alice's way", actor_subject=alice, access_scope=AccessScope.for_user(alice), **body)
    sid = str(mine["id"])
    assert (await submissions_service.get_procedure_submission(pool, sid, scope=AccessScope.for_user(alice))) is not None
    for scope in (AccessScope.for_user(bob), AccessScope.anonymous()):
        assert await submissions_service.get_procedure_submission(pool, sid, scope=scope) is None
        listed = await submissions_service.list_procedure_submissions(pool, scope=scope, goal_id=private_goal)
        assert listed == []
