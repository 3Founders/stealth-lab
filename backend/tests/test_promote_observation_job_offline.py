"""Offline proving tests for the observation -> claim hop's wiring.

CONTEXT: promote_observation_to_claim() (observations.py) was real, tested,
and had ZERO production callers. Registering it in JOB_HANDLERS alone would
not have helped either -- nothing ever created work for it.

UPDATED (trajectory-ingestion-hardening task): handle_normalize_trace_event
used to enqueue a promotion job for EVERY observation it persisted --
exactly the "raw telemetry auto-promoted into a Claim" anti-pattern the
task's Claims section forbids. It no longer does; deterministic
observations stay observations. `test_persisting_an_observation_does_not_
auto_enqueue_promotion` below is the regression guard for that removal.
The handler registration contract and the cost pre-check on
handle_promote_observation_to_claim (still a real, callable handler --
just no longer auto-invoked) are unchanged and still proved here.

The pre-check matters for cost, not just correctness: claims.py:179-180
computes an embedding (`embedder or Embedder()` -- a real Voyage call)
BEFORE claims.py:184-189 checks task_nodes and returns None. An
unresolvable promotion therefore spends one API call to produce nothing,
so the handler must bail out before reaching promote at all.

Fully offline: no database, no network. FakePool/FakeConn capture the real
SQL + params, same convention as test_trace_payload_cap.py.
"""
from __future__ import annotations

import json

import pytest

import app.services.ingestion_jobs as ij


# ---------------------------------------------------------------- fakes

class FakePool:
    """Captures execute/fetch/fetchrow calls. Deliberately not shared with
    other test modules -- this repo keeps its fakes per-file on purpose."""

    def __init__(self, row=None, fetch_rows=None):
        self._row = row
        self._fetch_rows = fetch_rows if fetch_rows is not None else []
        self.executed: list[tuple] = []
        self.fetched: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self._row

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self._fetch_rows

    async def fetchval(self, sql, *args):
        # Added when Option B introduced resolve_justification_episode():
        # the enqueue path now resolves a containing episode first. None
        # here means "no episode covers this event", which is the ordinary
        # case for a session episode assembly hasn't processed yet.
        self.fetched.append((sql, args))
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    # Added when the trace path started opening an IngestionContext (G1):
    # resolve_trace_ingestion_context -> open_ingestion_context uses
    # `async with tenant_transaction(pool, ...) as conn:` which needs
    # pool.acquire(). The fake conn just records SQL; open_ingestion_context
    # generates its own uuid7 id and ignores the RETURNING row.
    def acquire(self):
        pool = self

        class _Conn:
            async def execute(self, sql, *args):
                pool.executed.append((sql, args))

            async def fetchrow(self, sql, *args):
                pool.executed.append((sql, args))
                return {"id": "00000000-0000-0000-0000-000000000abc"}

            def transaction(self):
                class _Txn:
                    async def __aenter__(self_):
                        return None

                    async def __aexit__(self_, *exc):
                        return False

                return _Txn()

        class _AcquireCM:
            async def __aenter__(self_):
                return _Conn()

            async def __aexit__(self_, *exc):
                return False

        return _AcquireCM()


def _trace_row(tool_input):
    # session_id/timestamp are real trace_events columns; Option B's
    # resolve_justification_episode() reads them off the anchor row, so the
    # fake carries them rather than pretending the schema is narrower.
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "event_type": "tool_call",
        "tool_name": "Edit",
        "tool_input": tool_input,
        "tool_output": None,
        "owner_id": None,
        "visibility": "public",
        "session_id": "sess-fake",
        "timestamp": 1_700_000_000,
    }


# ------------------------------------------------- registration contract

def test_promotion_handler_is_registered():
    assert "promote_observation_to_claim" in ij.JOB_HANDLERS
    assert ij.JOB_HANDLERS["promote_observation_to_claim"] is (
        ij.handle_promote_observation_to_claim
    )


def test_normalize_handler_still_registered():
    """The new entry must not displace the existing one."""
    assert ij.JOB_HANDLERS["normalize_trace_event"] is ij.handle_normalize_trace_event


# --------------------------------------------------------- the enqueue

@pytest.mark.asyncio
async def test_persisting_an_observation_does_not_auto_enqueue_promotion(monkeypatch):
    """REGRESSION GUARD (trajectory-ingestion-hardening task, §1/§9): a
    deterministic observation must be persisted, and must NOT trigger an
    automatic 'promote_observation_to_claim' job -- raw structural
    telemetry ("Modified /repo/a.py") is not a reusable Claim just
    because it was recorded."""
    persisted = []

    async def fake_persist(pool, **kwargs):
        persisted.append(kwargs)
        return "obs-abc"

    monkeypatch.setattr(ij, "persist_observation", fake_persist)

    pool = FakePool(row=_trace_row(json.dumps({"file_path": "/repo/a.py"})))
    await ij.handle_normalize_trace_event(
        pool, {"trace_event_id": "11111111-1111-1111-1111-111111111111"}
    )

    assert len(persisted) == 1, "the observation itself must still be written"
    jobs = [c for c in pool.executed if "INSERT INTO ingestion_jobs" in c[0]]
    assert jobs == [], "deterministic observations must not auto-enqueue a promotion job"


@pytest.mark.asyncio
async def test_double_encoded_event_is_still_persisted_without_enqueue(monkeypatch):
    """The double-encode fix and the no-auto-promote fix compose: a
    double-encoded payload used to throw before any observation was
    persisted; now it must persist cleanly and still not auto-enqueue."""
    async def fake_persist(pool, **kwargs):
        return "obs-double"

    monkeypatch.setattr(ij, "persist_observation", fake_persist)

    double = json.dumps(json.dumps({"file_path": "/repo/b.py"}))
    pool = FakePool(row=_trace_row(double))
    await ij.handle_normalize_trace_event(
        pool, {"trace_event_id": "11111111-1111-1111-1111-111111111111"}
    )
    assert not any("INSERT INTO ingestion_jobs" in c[0] for c in pool.executed)


# ------------------------------------------------- the cost pre-check

@pytest.mark.asyncio
async def test_empty_task_ids_skips_before_any_spend(monkeypatch):
    """No task_ids -> must return without calling promote (which would
    compute an embedding before discovering it can't succeed)."""
    called = False

    async def boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("promote must not be reached without task_ids")

    monkeypatch.setattr(ij, "promote_observation_to_claim", boom)
    pool = FakePool()
    await ij.handle_promote_observation_to_claim(
        pool, {"observation_id": "obs-1", "task_ids": []}
    )
    assert called is False
    assert pool.fetched == [], "must not even query task_nodes when task_ids is empty"


@pytest.mark.asyncio
async def test_unresolvable_task_ids_skip_before_any_spend(monkeypatch):
    """task_ids present but matching no live task_node -> still no promote."""
    async def boom(*a, **k):
        raise AssertionError("promote must not be reached for unresolvable task_ids")

    monkeypatch.setattr(ij, "promote_observation_to_claim", boom)
    pool = FakePool(fetch_rows=[])  # no live task_nodes
    await ij.handle_promote_observation_to_claim(
        pool, {"observation_id": "obs-1", "task_ids": ["no-such-skill"]}
    )
    assert len(pool.fetched) == 1
    assert "task_nodes" in pool.fetched[0][0]


@pytest.mark.asyncio
async def test_resolvable_task_ids_do_call_promote(monkeypatch):
    seen = {}

    async def fake_promote(pool, *, observation_id, task_ids,
                           justification_episode_id=None):
        seen["observation_id"] = observation_id
        seen["task_ids"] = task_ids
        return "claim-1"

    monkeypatch.setattr(ij, "promote_observation_to_claim", fake_promote)
    pool = FakePool(fetch_rows=[{"?column?": 1}])
    await ij.handle_promote_observation_to_claim(
        pool, {"observation_id": "obs-9", "task_ids": ["real-skill"]}
    )
    assert seen == {"observation_id": "obs-9", "task_ids": ["real-skill"]}


@pytest.mark.asyncio
async def test_missing_observation_id_is_a_loud_error():
    """A malformed payload is a bug, not a silently-skipped job."""
    with pytest.raises(ValueError, match="missing observation_id"):
        await ij.handle_promote_observation_to_claim(FakePool(), {"task_ids": ["x"]})
