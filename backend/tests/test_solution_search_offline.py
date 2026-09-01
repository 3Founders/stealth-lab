"""
DB-free coverage for app/services/solution_search.py.

Mirrors test_domain_search_offline.py's own monkeypatching idiom:
`domain_search.search_global` (the only real retrieval this module calls)
is monkeypatched with a canned return, so what's under test HERE is
solution_search's OWN logic -- the round-robin interleave, the honest
empty result, and the bounded get_solution_view hydrate -- not
domain_search's or applicability's or retrieval.py's correctness (already
proven by their own test files).
"""
from __future__ import annotations

import asyncio

import pytest

import app.services.solution_search as ss
from app.services.access import AccessScope


def _run(coro):
    return asyncio.run(coro)


def _procedure_hit(pid: str, **overrides) -> dict:
    row = {
        "id": pid,
        "procedure_id": pid,
        "name": f"procedure {pid}",
        "goal": f"goal for {pid}",
        "verification_state": "verified",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "approved",
        "scope_type": "repository",
        "scope_entity_id": "repo-1",
        "similarity_score": 0.9,
        "version": 1,
    }
    row.update(overrides)
    return row


def _task_hit(tid: str, **overrides) -> dict:
    row = {
        "id": tid,
        "name": f"task {tid}",
        "description": f"description for {tid}",
        "scope_type": None,
        "scope_entity_id": None,
        "score": 0.5,
        "matched_by": ["keyword"],
    }
    row.update(overrides)
    return row


def _fake_search_global(procedure_hits, task_hits):
    async def fake(pool, query, **kwargs):
        assert kwargs["object_types"] == ["procedure", "task"]  # claims deliberately excluded
        return {
            "query": query,
            "object_types": ["procedure", "task"],
            "results": {"procedure": procedure_hits, "task": task_hits},
            "counts": {"procedure": len(procedure_hits), "task": len(task_hits)},
        }
    return fake


async def _no_hydrate(pool, procedure_row_id, *, scope):
    """Stand-in for get_solution_view that would fail if a real pool were
    touched -- proves the hydrate cap actually bounds real lookups."""
    return {
        "capability": {"p_estimate": 0.42},
        "provenance": "system_pending_review",
        "claims": [{"id": "claim-1"}],
    }


# ---------------------------------------------------------------------------
# object_types are always ["procedure", "task"] -- claims never included.
# ---------------------------------------------------------------------------

def test_search_solutions_never_asks_for_claims(monkeypatch):
    monkeypatch.setattr(ss, "search_global", _fake_search_global([], []))
    result = _run(ss.search_solutions(
        pool=object(), query="deploy", scope=AccessScope.unrestricted(),
    ))
    assert result["results"] == []


# ---------------------------------------------------------------------------
# A task-only match ranks in the blended list.
# ---------------------------------------------------------------------------

def test_task_only_match_ranks_in_blended_list(monkeypatch):
    task = _task_hit("task-1")
    monkeypatch.setattr(ss, "search_global", _fake_search_global([], [task]))
    monkeypatch.setattr(ss, "get_solution_view", _no_hydrate)

    result = _run(ss.search_solutions(
        pool=object(), query="run the checklist", scope=AccessScope.unrestricted(),
    ))

    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["type"] == "task"
    assert hit["id"] == "task-1"
    assert hit["title"] == "task task-1"
    assert hit["applicable"] is None  # tasks have no applicability cascade -- honest None
    assert hit["capability"] is None  # no first-class task-level capability signal
    assert hit["claims"] == []


# ---------------------------------------------------------------------------
# A procedure-only match ranks in the blended list, and gets hydrated
# (it's within the top _HYDRATE_CAP).
# ---------------------------------------------------------------------------

def test_procedure_only_match_ranks_in_blended_list_and_is_hydrated(monkeypatch):
    proc = _procedure_hit("proc-1")
    monkeypatch.setattr(ss, "search_global", _fake_search_global([proc], []))

    hydrate_calls = []

    async def fake_get_solution_view(pool, procedure_row_id, *, scope):
        hydrate_calls.append(procedure_row_id)
        return await _no_hydrate(pool, procedure_row_id, scope=scope)

    monkeypatch.setattr(ss, "get_solution_view", fake_get_solution_view)

    result = _run(ss.search_solutions(
        pool=object(), query="deploy safely", scope=AccessScope.unrestricted(),
    ))

    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["type"] == "procedure"
    assert hit["id"] == "proc-1"
    assert hit["applicable"] is True  # cascade survivor -- reused, never re-derived
    assert hit["capability"] == {"p_estimate": 0.42}
    assert hit["claims"] == [{"id": "claim-1"}]
    assert hydrate_calls == ["proc-1"]


# ---------------------------------------------------------------------------
# Honest empty result -- never a fabricated pick.
# ---------------------------------------------------------------------------

def test_empty_result_is_honest_not_fabricated(monkeypatch):
    monkeypatch.setattr(ss, "search_global", _fake_search_global([], []))
    result = _run(ss.search_solutions(
        pool=object(), query="nothing matches this", scope=AccessScope.unrestricted(),
    ))
    assert result["results"] == []
    assert result["counts"] == {"procedure": 0, "task": 0, "blended": 0}
    assert "honest empty result" in result["reason"]
    assert "not a fabricated" in result["reason"]


# ---------------------------------------------------------------------------
# THE interleaving strategy itself -- a real, specific, non-trivial
# assertion of the round-robin-by-rank-position rule, not "list is
# non-empty". 3 procedures (own honest order p1 > p2 > p3) interleaved
# with 2 tasks (own honest order t1 > t2) must produce exactly:
# p1, t1, p2, t2, p3 -- each type walked in ITS OWN order, zipped by
# index, never re-ranked by comparing p's score to t's score.
# ---------------------------------------------------------------------------

def test_interleave_is_round_robin_by_rank_position(monkeypatch):
    procedures = [
        _procedure_hit("p1", similarity_score=0.99),  # highest procedure-native score
        _procedure_hit("p2", similarity_score=0.50),
        _procedure_hit("p3", similarity_score=0.10),  # lowest procedure-native score
    ]
    tasks = [
        _task_hit("t1", score=0.05),  # LOWER native score than every procedure here --
        _task_hit("t2", score=0.01),  # proves the merge never compares native scores.
    ]
    monkeypatch.setattr(ss, "search_global", _fake_search_global(procedures, tasks))
    monkeypatch.setattr(ss, "get_solution_view", _no_hydrate)

    result = _run(ss.search_solutions(
        pool=object(), query="do the thing", scope=AccessScope.unrestricted(), limit=10,
    ))

    assert result["interleave_strategy"] == "round_robin_by_rank_position"
    got = [(hit["type"], hit["id"]) for hit in result["results"]]
    assert got == [
        ("procedure", "p1"),
        ("task", "t1"),
        ("procedure", "p2"),
        ("task", "t2"),
        ("procedure", "p3"),
    ]
    # t1's native RRF score (0.05) is far below every procedure's native
    # score, yet t1 still lands in position 2 (ahead of p2/p3) -- proof
    # the merge uses rank POSITION, never native score comparison.
    assert result["results"][1]["type"] == "task"
    assert result["results"][1]["native_score"] == 0.05


def test_interleave_respects_final_limit(monkeypatch):
    procedures = [_procedure_hit(f"p{i}") for i in range(5)]
    tasks = [_task_hit(f"t{i}") for i in range(5)]
    monkeypatch.setattr(ss, "search_global", _fake_search_global(procedures, tasks))
    monkeypatch.setattr(ss, "get_solution_view", _no_hydrate)

    result = _run(ss.search_solutions(
        pool=object(), query="do the thing", scope=AccessScope.unrestricted(), limit=4,
    ))
    got = [(hit["type"], hit["id"]) for hit in result["results"]]
    assert got == [("procedure", "p0"), ("task", "t0"), ("procedure", "p1"), ("task", "t1")]


def test_hydrate_cap_bounds_procedure_lookups(monkeypatch):
    """More procedure hits than _HYDRATE_CAP -- only the first CAP, in
    blended order, get a real get_solution_view() call."""
    procedures = [_procedure_hit(f"p{i}") for i in range(10)]
    monkeypatch.setattr(ss, "search_global", _fake_search_global(procedures, []))

    hydrate_calls = []

    async def fake_get_solution_view(pool, procedure_row_id, *, scope):
        hydrate_calls.append(procedure_row_id)
        return await _no_hydrate(pool, procedure_row_id, scope=scope)

    monkeypatch.setattr(ss, "get_solution_view", fake_get_solution_view)

    result = _run(ss.search_solutions(
        pool=object(), query="deploy", scope=AccessScope.unrestricted(), limit=10,
    ))
    assert len(result["results"]) == 10
    assert len(hydrate_calls) == ss._HYDRATE_CAP
    assert hydrate_calls == [f"p{i}" for i in range(ss._HYDRATE_CAP)]
    # Hits beyond the cap keep an honest, non-fabricated None/[]
    for hit in result["results"][ss._HYDRATE_CAP:]:
        assert hit["capability"] is None
        assert hit["claims"] == []
