"""
Offline tests for app/services/claim_temporal.py.

Deliberately thin, same posture test_claim_impact_offline.py documents
for itself: the real value here is two real SQL joins
(episode_links -> episodes -> agent_traces, and the t_valid backfill
query) that a hand-rolled fake can only prove "the right-looking string
was sent to the right table", not "Postgres actually joins these rows
correctly" -- that correctness is what tests/test_claim_temporal_e2e.py
proves for real, against real Postgres.

What IS worth proving offline: `get_claim_commit_history`'s own
orchestration (it calls the real `claims.get_claim_version_chain`
verbatim, never reimplements version-chain walking; it returns one dict
per version, not per commit; a version with zero episode_links comes
back with an honestly empty `commits` list) and
`what_was_current_as_of_commit`'s own selection logic (latest chain-order
version whose commits contain the target hash; `None` when nothing
matches or the claim doesn't resolve at all). Proven by monkeypatching
`claim_temporal.get_claim_version_chain` (the real seam this module
imports) and a minimal FakePool for the two follow-up queries this
module issues itself.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import claim_temporal


class FakePool:
    """Responds to the two real queries claim_temporal issues itself
    (t_valid backfill, keyed by id list; commits-per-version, keyed by a
    single claim id) by sniffing the SQL text -- same idiom other offline
    fakes in this repo use, not a shared fixture."""

    def __init__(self, t_valid_by_id: dict, commits_by_id: dict):
        self.t_valid_by_id = t_valid_by_id
        self.commits_by_id = commits_by_id

    async def fetch(self, query: str, *params):
        if "FROM knowledge_nodes WHERE id = ANY" in query:
            ids = params[0]
            return [
                {"id": i, "t_valid": self.t_valid_by_id[i]}
                for i in ids if i in self.t_valid_by_id
            ]
        if "FROM episode_links el" in query:
            claim_id = params[0]
            return list(self.commits_by_id.get(claim_id, []))
        raise AssertionError(f"unexpected query: {query}")


def _chain_row(claim_id: str, version, statement: str) -> dict:
    props = {"statement": statement}
    if version is not None:
        props["claim_version"] = version
    return {"id": claim_id, "properties": props}


def test_get_claim_commit_history_returns_empty_for_unresolvable_claim(monkeypatch):
    async def fake_chain(pool, claim_id):
        assert claim_id == "missing"
        return []

    monkeypatch.setattr(claim_temporal, "get_claim_version_chain", fake_chain)

    result = asyncio.run(claim_temporal.get_claim_commit_history(FakePool({}, {}), "missing"))
    assert result == []


def test_get_claim_commit_history_one_dict_per_version_with_honest_empty_commits(monkeypatch):
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)

    async def fake_chain(pool, claim_id):
        return [
            _chain_row("v1", None, "root statement"),   # root: no claim_version key
            _chain_row("v2", 2, "second statement"),
        ]

    monkeypatch.setattr(claim_temporal, "get_claim_version_chain", fake_chain)

    pool = FakePool(
        t_valid_by_id={"v1": t1, "v2": t2},
        commits_by_id={
            "v1": [],  # no real episode_links row at all -- honestly empty
            "v2": [{"commit_hash": "abc123", "repo": "r", "branch": "main"}],
        },
    )

    result = asyncio.run(claim_temporal.get_claim_commit_history(pool, "v1"))

    assert len(result) == 2
    assert result[0] == {
        "claim_id": "v1", "version": 1, "statement": "root statement",
        "t_valid": t1, "commits": [],
    }
    assert result[1] == {
        "claim_id": "v2", "version": 2, "statement": "second statement",
        "t_valid": t2,
        "commits": [{"commit_hash": "abc123", "repo": "r", "branch": "main"}],
    }


def test_what_was_current_as_of_commit_returns_none_when_claim_unresolvable(monkeypatch):
    async def fake_chain(pool, claim_id):
        return []

    monkeypatch.setattr(claim_temporal, "get_claim_version_chain", fake_chain)

    result = asyncio.run(
        claim_temporal.what_was_current_as_of_commit(FakePool({}, {}), "missing", commit_hash="deadbeef")
    )
    assert result is None


def test_what_was_current_as_of_commit_returns_none_when_no_version_matches(monkeypatch):
    async def fake_chain(pool, claim_id):
        return [_chain_row("v1", None, "s1")]

    monkeypatch.setattr(claim_temporal, "get_claim_version_chain", fake_chain)
    pool = FakePool(
        t_valid_by_id={"v1": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        commits_by_id={"v1": [{"commit_hash": "other", "repo": "r", "branch": "main"}]},
    )

    result = asyncio.run(
        claim_temporal.what_was_current_as_of_commit(pool, "v1", commit_hash="deadbeef")
    )
    assert result is None


def test_what_was_current_as_of_commit_picks_latest_chain_order_match(monkeypatch):
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 1, tzinfo=timezone.utc)

    async def fake_chain(pool, claim_id):
        return [
            _chain_row("v1", None, "s1"),
            _chain_row("v2", 2, "s2"),
            _chain_row("v3", 3, "s3"),
        ]

    monkeypatch.setattr(claim_temporal, "get_claim_version_chain", fake_chain)
    pool = FakePool(
        t_valid_by_id={"v1": t1, "v2": t2, "v3": t3},
        commits_by_id={
            # same commit_hash justified two versions (a real, if unusual,
            # shape -- e.g. two claims captured off the same episode) --
            # picks the LATER one, per docstring.
            "v1": [{"commit_hash": "shared", "repo": "r", "branch": "main"}],
            "v2": [{"commit_hash": "shared", "repo": "r", "branch": "main"}],
            "v3": [],
        },
    )

    result = asyncio.run(
        claim_temporal.what_was_current_as_of_commit(pool, "v1", commit_hash="shared")
    )
    assert result is not None
    assert result["claim_id"] == "v2"
    assert result["version"] == 2
