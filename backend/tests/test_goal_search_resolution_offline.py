from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import app.services.goals as goals_service
import app.services.retrieval_service as retrieval
from app.services.access import AccessScope


def _run(coro):
    return asyncio.run(coro)


def test_search_goals_defaults_to_all_resolution_states(monkeypatch):
    captured = {}

    async def fake_search(pool, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(retrieval, "search_goal_candidates", fake_search)
    _run(goals_service.search_goals(object(), query_text="find references"))

    assert captured["resolved"] == "all"
    assert captured["status"] is None


def test_search_goals_forwards_explicit_resolution_filter(monkeypatch):
    captured = {}

    async def fake_search(pool, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(retrieval, "search_goal_candidates", fake_search)
    _run(goals_service.search_goals(
        object(), query_text="find references", status="active", resolved="unresolved",
    ))

    assert captured["resolved"] == "unresolved"
    assert captured["status"] == "active"


def test_search_goal_candidates_filters_hydrated_rows_without_exposing_embedding(monkeypatch):
    resolved_at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    hits = [
        retrieval.Hit("g1", "find references", "find references", "K000"),
        retrieval.Hit("g2", "find references safely", "find references safely", "K000"),
    ]

    async def fake_legs(*args, **kwargs):
        return hits, len(hits), 0

    class _Pools:
        async def get(self, shard_id):
            return object()

    class _Hydration:
        rows = {
            "g1": {"id": "g1", "canonical_name": "find references", "resolved_at": None, "embedding": "[1,2]"},
            "g2": {"id": "g2", "canonical_name": "find references safely", "resolved_at": resolved_at, "embedding": "[3,4]"},
        }

    async def fake_hydrate(pools, id_to_shard, fetch):
        return _Hydration()

    monkeypatch.setattr(retrieval, "_legs", fake_legs)
    monkeypatch.setattr(retrieval, "pools_for", lambda pool: _Pools())
    monkeypatch.setattr(retrieval, "hydrate_rows", fake_hydrate)

    all_rows = _run(retrieval.search_goal_candidates(
        object(), query_text="find references", scope=AccessScope.unrestricted(),
    ))
    resolved_rows = _run(retrieval.search_goal_candidates(
        object(), query_text="find references", scope=AccessScope.unrestricted(),
        resolved="resolved", limit=1,
    ))

    assert [row["id"] for row in all_rows] == ["g1", "g2"]
    assert [row["id"] for row in resolved_rows] == ["g2"]
    first_page, first_has_more = _run(retrieval.search_goal_candidates_page(
        object(), query_text="find references", scope=AccessScope.unrestricted(),
        limit=1,
    ))
    assert [row["id"] for row in first_page] == ["g1"]
    assert first_has_more is True
    paged, has_more = _run(retrieval.search_goal_candidates_page(
        object(), query_text="find references", scope=AccessScope.unrestricted(),
        limit=1, offset=1,
    ))
    assert [row["id"] for row in paged] == ["g2"]
    assert has_more is False
    assert all("embedding" not in row for row in all_rows + resolved_rows + paged)
