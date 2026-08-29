"""The episode-arrived-late recovery path.

CONTEXT, measured on real dogfooding data 2026-08-29 (see
.scratch/research/founding-loop-real-data-proof.md): trace ingestion ran
BEFORE episode assembly, so resolve_justification_episode() -- which runs
at ENQUEUE time -- returned None for all 3,106 promotion jobs. Every one
completed as 'done' having correctly done nothing, and the substrate
ended with ONE claim instead of ~3,106. Handler right, queue right, loop
still dead: nothing re-offered the work once the anchor appeared.

Three properties are load-bearing and all three are pinned here:

  1. it enqueues ONLY observations that now resolve an episode,
  2. it NEVER double-enqueues (each promotion is a paid embedding call),
  3. it is OFF unless a caller passes an explicit positive limit.

Fully offline: a FakePool captures the emitted SQL and params, same
per-file-fake convention as test_promote_observation_job_offline.py.
"""
from __future__ import annotations

import json

import pytest

import app.services.ingestion_jobs as ij


class FakePool:
    """Returns a canned candidate set for the sweep SELECT and records
    every INSERT the function emits."""

    def __init__(self, rows):
        self._rows = rows
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self._rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _row(obs, event, episode):
    return {"observation_id": obs, "event_id": event, "episode_id": episode}


def _inserts(pool):
    return [c for c in pool.executed if "INSERT INTO ingestion_jobs" in c[0]]


# ------------------------------------------------------- the enqueue

@pytest.mark.asyncio
async def test_anchored_observation_is_requeued():
    pool = FakePool([_row("obs-1", "ev-1", "ep-1")])
    out = await ij.enqueue_pending_claim_promotions(pool, limit=10)

    assert out == {"examined": 1, "enqueued": 1, "still_unanchored": 0}
    ins = _inserts(pool)
    assert len(ins) == 1
    _, args = ins[0]
    assert args[0] == "promote_observation_to_claim"
    payload = json.loads(args[1])
    assert payload["observation_id"] == "obs-1"
    assert payload["justification_episode_id"] == "ep-1"
    assert payload["task_ids"] == []


@pytest.mark.asyncio
async def test_requeued_payload_is_marked_as_a_recovery():
    """A hand-reader of ingestion_jobs must be able to tell a recovery
    enqueue from the original inline one."""
    pool = FakePool([_row("obs-1", "ev-1", "ep-1")])
    await ij.enqueue_pending_claim_promotions(pool, limit=10)
    payload = json.loads(_inserts(pool)[0][1][1])
    assert payload["requeued_after_episode_assembly"] is True


@pytest.mark.asyncio
async def test_unanchored_observation_is_counted_not_enqueued():
    """Assembly still hasn't covered that session. Enqueuing anyway would
    spend an embedding call to produce nothing -- the exact waste the
    handler's own pre-check exists to avoid."""
    pool = FakePool([_row("obs-1", "ev-1", None)])
    out = await ij.enqueue_pending_claim_promotions(pool, limit=10)

    assert out == {"examined": 1, "enqueued": 0, "still_unanchored": 1}
    assert _inserts(pool) == []


@pytest.mark.asyncio
async def test_mixed_batch_splits_correctly():
    pool = FakePool([
        _row("obs-1", "ev-1", "ep-1"),
        _row("obs-2", "ev-2", None),
        _row("obs-3", "ev-3", "ep-3"),
    ])
    out = await ij.enqueue_pending_claim_promotions(pool, limit=10)
    assert out == {"examined": 3, "enqueued": 2, "still_unanchored": 1}
    assert len(_inserts(pool)) == 2


# --------------------------------------------- the no-double-spend SQL

@pytest.mark.asyncio
async def test_limit_is_passed_to_the_query_not_applied_after():
    """The cap must bound what the DATABASE returns. Fetching thousands
    of rows and slicing in Python would still scan the whole corpus."""
    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=7)
    sql, args = pool.fetched[0]
    assert args == (7,)
    assert "LIMIT $1" in sql


@pytest.mark.asyncio
async def test_query_excludes_observations_that_already_have_a_claim():
    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "claim_sources" in sql
    assert "NOT EXISTS" in sql


@pytest.mark.asyncio
async def test_query_excludes_observations_with_a_promotion_already_queued():
    """Double-enqueueing is not merely untidy -- it is a duplicated paid
    embedding call and a duplicate claim."""
    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "'pending', 'processing'" in sql
    assert "promote_observation_to_claim" in sql


@pytest.mark.asyncio
async def test_episode_selection_matches_resolve_justification_episode():
    """The two must agree on which episode wins, or a requeued claim gets
    anchored to a DIFFERENT episode than the inline path would have
    chosen. NULLS LAST picks the innermost/child episode; NULLS FIRST
    would pick the outermost parent (the correction recorded in
    resolve_justification_episode's own docstring)."""
    import inspect

    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=1)
    sweep_sql = pool.fetched[0][0]
    resolver_src = inspect.getsource(ij.resolve_justification_episode)

    ordering = "ORDER BY ep.parent_episode_id NULLS LAST, ep.start_ts DESC"
    assert ordering in sweep_sql
    assert "parent_episode_id NULLS LAST" in resolver_src
    assert "start_ts DESC" in resolver_src


@pytest.mark.asyncio
async def test_anchored_rows_are_ordered_ahead_of_unanchored():
    """REGRESSION GUARD, found against the real corpus. The first cut
    ordered purely by newest-first and reported examined 25 / enqueued 0
    / still_unanchored 25 -- the newest observations are precisely the
    ones episode assembly has not covered yet, so a bounded sweep burned
    its whole limit on rows it could not act on and never reached the
    backlog it existed to drain."""
    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert 'ORDER BY (episode_id IS NULL), "timestamp" DESC' in sql, (
        "anchored candidates must sort first, or the limit is spent on no-ops"
    )


@pytest.mark.asyncio
async def test_anchor_is_the_earliest_event():
    """resolve_justification_episode() anchors on the observation's
    EARLIEST event; the sweep must not silently pick a different one."""
    pool = FakePool([])
    await ij.enqueue_pending_claim_promotions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "DISTINCT ON (oe.observation_id)" in sql
    assert 'ORDER BY oe.observation_id, te."timestamp" ASC' in sql


# ------------------------------------------------- off unless asked for

@pytest.mark.asyncio
async def test_zero_limit_enqueues_nothing():
    """`--promote-limit 0` is the default and must be a true no-op at the
    database level, not just an empty result."""
    # Rows are deliberately NON-empty: if the guard were missing, this
    # would enqueue one and the assertions below would fail. An empty
    # FakePool would pass either way and prove nothing.
    pool = FakePool([_row("obs-1", "ev-1", "ep-1")])
    out = await ij.enqueue_pending_claim_promotions(pool, limit=0)
    assert out == {"examined": 0, "enqueued": 0, "still_unanchored": 0}
    assert _inserts(pool) == []
    assert pool.fetched == [], "limit<=0 must not even run the sweep query"


def test_runner_defaults_to_disabled():
    """The CLI default must be 0. A default sweep would spend real money
    on the next ordinary --once run anybody happens to make."""
    import ast
    import pathlib

    src = pathlib.Path(ij.__file__).parents[2] / "scripts" / "run_ingestion.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "add_argument"
                and node.args
                and getattr(node.args[0], "value", None) == "--promote-limit"):
            for kw in node.keywords:
                if kw.arg == "default":
                    found.append(kw.value.value)
    assert found == [0], f"--promote-limit default must be 0, got {found}"


def test_run_once_signature_keeps_it_opt_in():
    import inspect
    import importlib.util
    import pathlib

    src = pathlib.Path(ij.__file__).parents[2] / "scripts" / "run_ingestion.py"
    spec = importlib.util.spec_from_file_location("_run_ingestion_probe", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sig = inspect.signature(mod._run_once)
    assert sig.parameters["promote_limit"].default == 0
