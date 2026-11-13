"""Add a Goal and retrieve it (docs/goal_router.md): the MCP tools, no network, no database."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def run(coro):
    return asyncio.run(coro)


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = SimpleNamespace(lifespan_context={"pool": pool})


def _fake_embedder():
    class Embedder:
        def __init__(self, *a, **k):
            pass

    return Embedder


def test_add_goal_writes_for_the_signed_in_person_and_refreshes_the_catalog(monkeypatch):
    from app.mcp_server import server as srv
    from app.services import embeddings, goals
    from app.tool_routing import goal_catalog

    seen = {}
    refreshed = []

    async def create(pool, **kwargs):
        seen.update(kwargs)
        seen["pool"] = pool
        return {"outcome": "created", "goal": {"id": "g-1", "canonical_name": "Send a Slack message"}}

    async def refresh_now(pool):
        refreshed.append(pool)
        return True

    monkeypatch.setattr(srv, "_kell_owner", lambda: "user-1")
    monkeypatch.setattr(embeddings, "Embedder", _fake_embedder())
    monkeypatch.setattr(goals, "create_goal_from_user", create)
    monkeypatch.setattr(goal_catalog, "refresh_now", refresh_now)

    pool = object()
    out = json.loads(run(srv.add_goal(
        FakeContext(pool), canonical_name="Send a Slack message",
        rationale="the team lives in Slack", objective="the message is posted")))

    assert out["outcome"] == "created" and out["goal"]["id"] == "g-1"
    assert seen["owner_id"] == "user-1" and seen["pool"] is pool
    assert seen["canonical_name"] == "Send a Slack message"
    assert seen["objective"] == "the message is posted"
    assert refreshed == [pool], "a created Goal must be in the catalog before the next /rank"


def test_add_goal_returns_near_matches_and_writes_nothing(monkeypatch):
    from app.mcp_server import server as srv
    from app.services import embeddings, goals
    from app.tool_routing import goal_catalog

    refreshed = []

    async def create(pool, **kwargs):
        return {"outcome": "near_matches",
                "candidates": [{"id": "g-0", "canonical_name": "Send a message"}]}

    async def refresh_now(pool):
        refreshed.append(pool)
        return True

    monkeypatch.setattr(srv, "_kell_owner", lambda: "user-1")
    monkeypatch.setattr(embeddings, "Embedder", _fake_embedder())
    monkeypatch.setattr(goals, "create_goal_from_user", create)
    monkeypatch.setattr(goal_catalog, "refresh_now", refresh_now)

    out = json.loads(run(srv.add_goal(
        FakeContext(object()), canonical_name="Send a message",
        rationale="why", objective="done")))

    assert out["outcome"] == "near_matches" and out["candidates"][0]["id"] == "g-0"
    assert refreshed == [], "nothing was written, so there is nothing to refresh"


def test_add_goal_refuses_without_a_signed_in_person(monkeypatch):
    from app.mcp_server import server as srv

    def no_owner():
        raise PermissionError("forbidden")

    monkeypatch.setattr(srv, "_kell_owner", no_owner)

    out = run(srv.add_goal(FakeContext(object()), canonical_name="Do X",
                              rationale="why", objective="done"))
    assert out.startswith("REFUSED:") and "sign in" in out


def test_search_goals_finds_a_goal_the_catalog_already_holds(monkeypatch):
    from app.mcp_server import server as srv
    from app.tool_routing import goal_catalog

    catalog = goal_catalog.Catalog(
        entries=[goal_catalog.GoalEntry(
            id="g-1", canonical_name="Send a Slack message", description="chat",
            status="candidate", home_shard="K000",
            norm=goal_catalog.normalize_goal_name("Send a Slack message"),
            name_tokens=goal_catalog.tokens("Send a Slack message"),
            desc_tokens=goal_catalog.tokens("chat"))],
        by_norm={}, aliases={}, idf={},
    )

    async def load(pool, force=False):
        return catalog

    monkeypatch.setattr(goal_catalog, "load", load)

    out = json.loads(run(srv.search_goals(FakeContext(object()), query="slack message", limit=5)))
    assert [g["id"] for g in out["goals"]] == ["g-1"]
    assert out["goals"][0]["matched_by"] == "fts"


def test_search_goals_falls_back_to_the_store_when_the_catalog_has_no_hit(monkeypatch):
    from app.mcp_server import server as srv
    from app.services import goals
    from app.tool_routing import goal_catalog

    class Empty:
        def match(self, query, limit=10):
            return []

    async def load(pool, force=False):
        return Empty()

    async def store(pool, *, query_text, scope, limit):
        return [{"id": "g-2", "canonical_name": "Reconcile the ledger", "description": "",
                 "status": "active"}]

    monkeypatch.setattr(goal_catalog, "load", load)
    monkeypatch.setattr(srv, "_kell_owner", lambda: "user-1")
    monkeypatch.setattr(goals, "search_goals", store)

    out = json.loads(run(srv.search_goals(FakeContext(object()), query="ledger")))
    assert out["goals"] == [{"id": "g-2", "canonical_name": "Reconcile the ledger",
                             "description": "", "status": "active", "matched_by": "store"}]


def test_search_goals_with_no_query_answers_empty(monkeypatch):
    from app.mcp_server import server as srv

    out = json.loads(run(srv.search_goals(FakeContext(object()), query="  ")))
    assert out == {"query": "", "goals": []}
