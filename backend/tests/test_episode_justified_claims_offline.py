"""Option B: a claim may be anchored by an EPISODE instead of a task_node.

CONTEXT: capture_claim's `if not rows: return None` fired ahead of the
`justification_episode_id is not None` branch further down, making that
branch dead code on the task-less path. A trace-derived observation has no
task_node to resolve against, so observation -> claim could never complete
at all -- measured 2026-08-28: 2779 promotion jobs ran, 0 claims produced.

Both inputs missing must STILL return None. That safety net is deliberate
and these tests exist partly to stop it regressing.

Fully offline: FakePool/FakeConn capture real SQL + params; a FakeEmbedder
keeps capture_claim's embedding call off the network (it would otherwise
construct a real Voyage-backed Embedder).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import app.services.ingestion_jobs as ij
from app.services.claims import capture_claim


# ---------------------------------------------------------------- fakes

class FakeEmbedder:
    async def embed_one(self, text, input_type=None):
        return [0.0] * 1024


class FakeConn:
    def __init__(self, task_rows=None):
        self._task_rows = task_rows if task_rows is not None else []
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []
        self.inserted_node = False

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self._task_rows

    async def fetchval(self, sql, *args):
        if "INSERT INTO knowledge_nodes" in sql:
            self.inserted_node = True
            return "00000000-0000-0000-0000-0000000000cc"
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    @asynccontextmanager
    async def transaction(self):
        yield


class FakePool:
    def __init__(self, conn=None, rows=None, val=None):
        self._conn = conn
        self._rows = rows or []
        self._val = val
        self.queries: list[tuple] = []

    @asynccontextmanager
    async def acquire(self):
        yield self._conn

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        return self._rows[0] if self._rows else None

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return self._val

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return self._rows

    async def execute(self, sql, *args):
        self.queries.append((sql, args))


# ------------------------------------------- 1. the reordered gate

@pytest.mark.asyncio
async def test_episode_only_claim_is_written_with_zero_task_ids():
    """The whole point of Option B."""
    conn = FakeConn(task_rows=[])
    claim_id = await capture_claim(
        FakePool(conn), statement="a real observation label", task_ids=[],
        justification_episode_id="11111111-1111-1111-1111-111111111111",
        embedder=FakeEmbedder(),
    )
    assert claim_id is not None, "an episode-anchored claim must be written"
    assert conn.inserted_node, "the knowledge_nodes row must be inserted"
    links = [q for q in conn.executed if "episode_links" in q[0]]
    assert len(links) == 1, "the episode_links row must be written"


@pytest.mark.asyncio
async def test_episode_only_claim_writes_no_task_edge():
    """No task_nodes matched, so the PRODUCES/CLAIM_OF loop must no-op --
    an episode-anchored claim simply carries no task edge."""
    conn = FakeConn(task_rows=[])
    await capture_claim(
        FakePool(conn), statement="x", task_ids=[],
        justification_episode_id="11111111-1111-1111-1111-111111111111",
        embedder=FakeEmbedder(),
    )
    assert not [q for q in conn.executed if "CLAIM_OF" in q[0]]


@pytest.mark.asyncio
async def test_both_anchors_missing_still_returns_none():
    """THE safety net. Must not regress."""
    conn = FakeConn(task_rows=[])
    claim_id = await capture_claim(
        FakePool(conn), statement="x", task_ids=[],
        justification_episode_id=None, embedder=FakeEmbedder(),
    )
    assert claim_id is None
    assert not conn.inserted_node, "nothing may be written with no anchor at all"


@pytest.mark.asyncio
async def test_task_anchored_path_is_unchanged():
    """Regression: the pre-existing task path must behave exactly as before."""
    conn = FakeConn(task_rows=[{"id": "task-row-1"}])
    claim_id = await capture_claim(
        FakePool(conn), statement="x", task_ids=["real-skill"],
        justification_episode_id=None, embedder=FakeEmbedder(),
    )
    assert claim_id is not None
    assert len([q for q in conn.executed if "CLAIM_OF" in q[0]]) == 1
    assert not [q for q in conn.executed if "episode_links" in q[0]]


# ------------------------------- 2. episode resolution + nesting order

def _order_key(row):
    """The exact sort the resolution SQL expresses, as a Python key:
    `ORDER BY parent_episode_id NULLS LAST, start_ts DESC`.
    In Postgres, NULLS LAST on an ASC sort puts non-null (children) first."""
    return (row["parent_episode_id"] is None, -row["start_ts"])


def test_nesting_order_picks_the_innermost_episode():
    """The kickoff proposed `NULLS FIRST`, which is INVERTED: a parent is
    exactly the row whose parent_episode_id IS NULL, so NULLS FIRST selects
    the OUTERMOST episode. Verified against the live database too:
      NULLS FIRST -> PARENT, NULLS LAST -> CHILD.
    """
    parent = {"id": "parent", "parent_episode_id": None, "start_ts": 1000}
    child = {"id": "child", "parent_episode_id": "parent", "start_ts": 1020}
    winner = sorted([parent, child], key=_order_key)[0]
    assert winner["id"] == "child", "the innermost/most specific episode must win"


def test_nesting_order_tiebreaks_siblings_by_latest_start():
    """Among siblings at the same depth, the tightest-fitting (latest
    starting) span wins."""
    early = {"id": "early", "parent_episode_id": "p", "start_ts": 1000}
    late = {"id": "late", "parent_episode_id": "p", "start_ts": 1050}
    assert sorted([early, late], key=_order_key)[0]["id"] == "late"


@pytest.mark.asyncio
async def test_resolution_sql_uses_nulls_last_not_nulls_first():
    """Pins the correction so nobody restores the inverted ordering."""
    pool = FakePool(rows=[{"session_id": "s1", "timestamp": 123}], val="ep-1")
    got = await ij.resolve_justification_episode(pool, "obs-1")
    assert got == "ep-1"
    episode_sql = [q[0] for q in pool.queries if "FROM episodes" in q[0]][0]
    assert "NULLS LAST" in episode_sql
    assert "NULLS FIRST" not in episode_sql


@pytest.mark.asyncio
async def test_resolution_returns_none_when_observation_has_no_events():
    pool = FakePool(rows=[], val=None)
    assert await ij.resolve_justification_episode(pool, "obs-1") is None


# ---------------------------------------------- 3. handler acceptance

@pytest.mark.asyncio
async def test_handler_proceeds_on_episode_alone(monkeypatch):
    seen = {}

    async def fake_promote(pool, *, observation_id, task_ids, justification_episode_id):
        seen.update(observation_id=observation_id, task_ids=task_ids,
                    justification_episode_id=justification_episode_id)
        return "claim-1"

    monkeypatch.setattr(ij, "promote_observation_to_claim", fake_promote)
    await ij.handle_promote_observation_to_claim(FakePool(), {
        "observation_id": "obs-1", "task_ids": [],
        "justification_episode_id": "ep-9",
    })
    assert seen["justification_episode_id"] == "ep-9"
    assert seen["task_ids"] == []


@pytest.mark.asyncio
async def test_handler_still_skips_when_neither_anchor_present(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not spend an embedding with no anchor")

    monkeypatch.setattr(ij, "promote_observation_to_claim", boom)
    await ij.handle_promote_observation_to_claim(FakePool(), {
        "observation_id": "obs-1", "task_ids": [], "justification_episode_id": None,
    })


@pytest.mark.asyncio
async def test_unresolvable_task_ids_fall_back_to_the_episode(monkeypatch):
    """task_ids present but matching nothing, episode present -> proceed
    with an empty task list rather than skipping."""
    seen = {}

    async def fake_promote(pool, *, observation_id, task_ids, justification_episode_id):
        seen.update(task_ids=task_ids, justification_episode_id=justification_episode_id)
        return "claim-1"

    monkeypatch.setattr(ij, "promote_observation_to_claim", fake_promote)
    await ij.handle_promote_observation_to_claim(FakePool(rows=[]), {
        "observation_id": "obs-1", "task_ids": ["no-such-skill"],
        "justification_episode_id": "ep-9",
    })
    assert seen == {"task_ids": [], "justification_episode_id": "ep-9"}
