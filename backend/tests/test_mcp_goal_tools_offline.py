"""
Offline (no real DB, no real network) thin-wrapper tests for the five
Goal-surface MCP tools: search_goals, inspect_goal, list_goal_procedures,
list_goal_implementations, create_goal. Same philosophy as
test_mcp_implementation_selection_tools_offline.py -- these do NOT
re-test app/services/goals.py's own dedup/search/scope logic (covered by
tests/test_goals_offline.py); they test the wrapper layer: JSON
parsing/validation, embedding opt-in gating, scope threading, and
correct pass-through of the underlying function's result.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv


class _FakePool:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetch(self, sql, *params):
        return self._rows


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool or _FakePool())


# --- search_goals --------------------------------------------------------

def test_search_goals_lexical_only_by_default_never_touches_the_embedder(monkeypatch):
    captured = {}

    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        captured["query_embedding"] = query_embedding
        return [{"id": "g1", "canonical_name": "find references"}]

    class _ExplodingEmbedder:
        def __init__(self):
            raise AssertionError("Embedder must not be constructed when semantic=False")

    monkeypatch.setattr("app.services.goals.search_goals", fake_search)
    monkeypatch.setattr("app.services.embeddings.Embedder", _ExplodingEmbedder)
    ctx = FakeContext()
    raw = asyncio.run(srv.search_goals(query="find refs", ctx=ctx))
    assert captured["query_embedding"] is None
    assert json.loads(raw) == [{"id": "g1", "canonical_name": "find references"}]


def test_search_goals_semantic_true_computes_a_real_embedding(monkeypatch):
    captured = {}

    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        captured["query_embedding"] = query_embedding
        return []

    class _FakeEmbedder:
        async def embed_one_with_metadata(self, text, input_type="document"):
            captured["embed_input_type"] = input_type
            return [0.1, 0.2], object()

    monkeypatch.setattr("app.services.goals.search_goals", fake_search)
    monkeypatch.setattr("app.services.embeddings.Embedder", _FakeEmbedder)
    ctx = FakeContext()
    asyncio.run(srv.search_goals(query="find refs", ctx=ctx, semantic=True))
    assert captured["query_embedding"] == [0.1, 0.2]
    assert captured["embed_input_type"] == "query"


def test_search_goals_narrows_by_scope_type_after_the_scope_checked_fetch(monkeypatch):
    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        return [
            {"id": "g1", "canonical_name": "x", "scope_type": "global", "scope_entity_id": None},
            {"id": "g2", "canonical_name": "y", "scope_type": "project", "scope_entity_id": "repo-a"},
        ]

    monkeypatch.setattr("app.services.goals.search_goals", fake_search)
    ctx = FakeContext()
    raw = asyncio.run(srv.search_goals(
        query="x", ctx=ctx, scope_type="project", scope_entity_id="repo-a",
    ))
    result = json.loads(raw)
    assert [r["id"] for r in result] == ["g2"]


# --- inspect_goal ----------------------------------------------------------

def test_inspect_goal_returns_null_for_a_missing_or_invisible_row(monkeypatch):
    async def fake_get(pool, goal_id, *, scope):
        return None

    monkeypatch.setattr("app.services.goals.get_goal", fake_get)
    ctx = FakeContext()
    raw = asyncio.run(srv.inspect_goal(goal_id="missing", ctx=ctx))
    assert json.loads(raw) is None


def test_inspect_goal_passes_through_the_full_record(monkeypatch):
    async def fake_get(pool, goal_id, *, scope):
        return {"id": goal_id, "canonical_name": "x", "procedures": []}

    monkeypatch.setattr("app.services.goals.get_goal", fake_get)
    ctx = FakeContext()
    raw = asyncio.run(srv.inspect_goal(goal_id="g1", ctx=ctx))
    assert json.loads(raw)["id"] == "g1"


# --- list_goal_procedures -----------------------

def test_list_goal_procedures_passes_through_real_rows():
    pool = _FakePool([{"id": "p1", "name": "grep-based search"}])
    ctx = FakeContext(pool)
    raw = asyncio.run(srv.list_goal_procedures(goal_id="g1", ctx=ctx))
    assert json.loads(raw) == [{"id": "p1", "name": "grep-based search"}]


# --- create_goal -----------------------------------------------------------

def test_create_goal_surfaces_v0_violations_as_refused(monkeypatch):
    from app.services.v0_gate import V0Violation

    async def fake_create(pool, **kw):
        raise V0Violation("V0: scope_type is required")

    monkeypatch.setattr("app.services.goals.create_goal_from_user", fake_create)
    ctx = FakeContext()
    raw = asyncio.run(srv.create_goal(canonical_name="x", ctx=ctx))
    assert raw.startswith("REFUSED:")


def test_create_goal_default_uses_embeddings(monkeypatch):
    captured = {}

    async def fake_create(pool, *, canonical_name, description, scope_type, scope_entity_id,
                           owner_id, embedder, allow_create_anyway):
        captured["embedder"] = embedder
        captured["allow_create_anyway"] = allow_create_anyway
        return {"outcome": "created", "goal": {"id": "g1"}}

    class _FakeEmbedder:
        pass

    monkeypatch.setattr("app.services.goals.create_goal_from_user", fake_create)
    monkeypatch.setattr("app.services.embeddings.Embedder", _FakeEmbedder)
    ctx = FakeContext()
    raw = asyncio.run(srv.create_goal(canonical_name="reconcile schema drift", ctx=ctx))
    assert isinstance(captured["embedder"], _FakeEmbedder)
    assert captured["allow_create_anyway"] is False
    assert json.loads(raw) == {"outcome": "created", "goal": {"id": "g1"}}


def test_create_goal_use_embeddings_false_skips_the_embedder(monkeypatch):
    captured = {}

    async def fake_create(pool, *, canonical_name, description, scope_type, scope_entity_id,
                           owner_id, embedder, allow_create_anyway):
        captured["embedder"] = embedder
        return {"outcome": "created", "goal": {"id": "g1"}}

    class _ExplodingEmbedder:
        def __init__(self):
            raise AssertionError("Embedder must not be constructed when use_embeddings=False")

    monkeypatch.setattr("app.services.goals.create_goal_from_user", fake_create)
    monkeypatch.setattr("app.services.embeddings.Embedder", _ExplodingEmbedder)
    ctx = FakeContext()
    asyncio.run(srv.create_goal(canonical_name="x", ctx=ctx, use_embeddings=False))
    assert captured["embedder"] is None


def test_create_goal_returns_near_matches_without_allow_create_anyway(monkeypatch):
    async def fake_create(pool, **kw):
        return {"outcome": "near_matches", "candidates": [{"id": "existing-1"}]}

    monkeypatch.setattr("app.services.goals.create_goal_from_user", fake_create)
    ctx = FakeContext()
    raw = asyncio.run(srv.create_goal(canonical_name="x", ctx=ctx, use_embeddings=False))
    result = json.loads(raw)
    assert result["outcome"] == "near_matches"
    assert result["candidates"][0]["id"] == "existing-1"
