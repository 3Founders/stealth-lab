"""
Offline proving suite for HARDENING H3 -- governance.py's collector
treatment of the rate limiter (in-process token bucket enforcement +
buffered rate_limit_events ledger flush).

A fake pool records every statement (normalized SQL + params) and a
fake clock drives every time decision, so these tests prove exactly the
things a live DB cannot isolate:

  - bucket behavior (capacity, continuous refill, key isolation,
    concurrency atomicity),
  - that the hot path pays (almost) zero SQL below the drain thresholds,
  - flush failure -> closed door (fail-closed on infra error),
  - the replay-or-block rule (events retained, replayed verbatim with
    original admission timestamps, door reopens only after success),
  - SQL CONTENT of the ledger writes and the retention sweep,
  - that retention-sweep failure never closes the door.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

import app.services.governance as governance
from app.services.governance import (
    DEGRADED_RETRY_AFTER_SECONDS,
    RATE_EVENT_RETENTION,
    RateLimit,
    RateLimiter,
    RateLimitExceeded,
)

START = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
# Comfortably above LEDGER_FLUSH_RETRY_INTERVAL's 2s default so a healed
# pool is actually retried on the next denied call.
RETRY_GAP = timedelta(seconds=5)


class FakeClock:
    """Single source of time for buckets, ledger timestamps and cadences."""

    def __init__(self, start: datetime = START):
        self.now = start

    def advance(self, delta: timedelta | None = None, **kw) -> datetime:
        self.now += delta if delta is not None else timedelta(**kw)
        return self.now

    def __call__(self) -> datetime:
        return self.now


class FakeConn:
    def __init__(self, pool: "FakePool"):
        self._pool = pool

    async def executemany(self, sql, args_seq):
        norm = " ".join(sql.split())
        rows = [tuple(a) for a in args_seq]
        self._pool.executemany_calls.append((norm, rows))
        if self._pool.on_executemany is not None:
            self._pool.on_executemany(sql, rows)
        if self._pool.fail_executemany:
            raise RuntimeError("ledger unwritable")

    async def execute(self, sql, *args):
        norm = " ".join(sql.split())
        self._pool.execute_calls.append((norm, tuple(args)))
        if self._pool.fail_execute:
            raise RuntimeError("housekeeping unavailable")
        return self._pool.execute_result


class FakePool:
    """
    Records every statement. fail_executemany simulates Postgres being
    unreachable at LEDGER-WRITE time; fail_execute simulates it failing
    only at RETENTION-SWEEP time. on_executemany lets a test run code at
    the moment the batch write is in flight.
    """

    def __init__(self, *, fail_executemany=False, fail_execute=False,
                 execute_result="DELETE 7"):
        self.fail_executemany = fail_executemany
        self.fail_execute = fail_execute
        self.execute_result = execute_result
        self.on_executemany = None
        self.executemany_calls = []
        self.execute_calls = []
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield FakeConn(self)

    async def execute(self, sql, *args):
        """Pool-level execute (purge_old's entry point; drains use conn)."""
        self.execute_calls.append((" ".join(sql.split()), tuple(args)))
        if self.fail_execute:
            raise RuntimeError("housekeeping unavailable")
        return self.execute_result


CHAT_LIMIT = dict(max_requests=5, window=timedelta(hours=1))


def limiter(pool, clock=None, *, max_requests=CHAT_LIMIT["max_requests"],
            window=CHAT_LIMIT["window"]) -> RateLimiter:
    return RateLimiter(
        pool,
        limits={"chat": RateLimit(max_requests=max_requests, window=window)},
        clock=clock or FakeClock(),
    )


def admits(lim: RateLimiter, key="viewer:alice", endpoint="chat", times=1):
    for _ in range(times):
        asyncio.run(lim.check_and_record(key, endpoint))


def denies(lim: RateLimiter, key="viewer:alice", endpoint="chat") -> RateLimitExceeded:
    try:
        asyncio.run(lim.check_and_record(key, endpoint))
    except RateLimitExceeded as exc:
        return exc
    raise AssertionError("expected RateLimitExceeded, call was admitted")


def all_flushed_rows(pool) -> list:
    return [row for _, rows in pool.executemany_calls for row in rows]


# --- bucket behavior -------------------------------------------------------


def test_capacity_admissions_pass_then_denial_carries_retry_after():
    lim = limiter(FakePool())
    admits(lim, times=5)
    exc = denies(lim)
    assert 0 < exc.retry_after_seconds <= 3600
    assert "chat" in str(exc)


def test_refill_is_continuous_not_a_window_reset():
    """Half the window back refills exactly one token (2/hour capacity
    bucket): one more admission passes, then denial again -- unlike the
    old fixed-window counter, which would have granted two."""
    lim = limiter(FakePool(), max_requests=2, window=timedelta(seconds=3600))
    clock = lim._clock
    admits(lim, times=2)
    denies(lim)
    clock.advance(seconds=1801)  # ~1 token of refill (margin over exact float math)
    admits(lim)
    denies(lim)


def test_scope_keys_are_isolated_buckets():
    lim = limiter(FakePool(), max_requests=1, window=timedelta(hours=1))
    admits(lim, key="viewer:a", times=1)
    denies(lim, key="viewer:a")
    admits(lim, key="viewer:b", times=1)  # untouched by a's exhaustion


def test_concurrent_calls_admit_exactly_capacity():
    """The critical section holds no awaits, so N gathered coroutines
    must produce exactly `capacity` admissions -- the same guarantee the
    old advisory-lock transaction gave, now without leaving the process."""
    lim = limiter(FakePool())

    async def burst():
        return await asyncio.gather(
            *(lim.check_and_record("viewer:racer", "chat") for _ in range(10)),
            return_exceptions=True,
        )

    outcomes = asyncio.run(burst())
    passed = sum(1 for o in outcomes if not isinstance(o, Exception))
    denied = [o for o in outcomes if isinstance(o, RateLimitExceeded)]
    assert passed == 5
    assert len(denied) == 5


# --- hot path cost: the headline -------------------------------------------


def test_hot_path_pays_zero_sql_below_drain_thresholds(monkeypatch):
    """THE H3 payoff: ten requests inside the freshness window and far
    below the drain threshold touch Postgres exactly ONCE -- the
    unconditional first-drain that establishes ledger reachability --
    versus V2's lock+COUNT+INSERT on every single request."""
    monkeypatch.setattr(governance, "LEDGER_FLUSH_THRESHOLD", 32)
    pool = FakePool()
    lim = limiter(pool, max_requests=50, window=timedelta(hours=1))
    admits(lim, times=10)
    assert pool.acquire_count == 1
    assert len(pool.executemany_calls[0][1]) == 1  # just request #1's event


def test_threshold_crossing_drains_all_pending_in_one_batch(monkeypatch):
    monkeypatch.setattr(governance, "LEDGER_FLUSH_THRESHOLD", 4)
    pool = FakePool()
    lim = limiter(pool, max_requests=50, window=timedelta(hours=1))
    admits(lim, times=6)
    assert [len(rows) for _, rows in pool.executemany_calls] == [1, 4]
    assert lim.pending_count == 1


def test_trickle_traffic_still_reaches_the_ledger_via_freshness_bound(monkeypatch):
    """Below threshold forever? The oldest pending event aging past
    LEDGER_MAX_FLUSH_DELAY forces a drain, so the audit ledger can't lag
    indefinitely under trickle traffic."""
    monkeypatch.setattr(governance, "LEDGER_MAX_FLUSH_DELAY", timedelta(seconds=30))
    pool = FakePool()
    lim = limiter(pool)
    clock = lim._clock

    admits(lim)                    # t=0: reachability drain flushes event 1
    clock.advance(seconds=31)
    admits(lim)                    # appends event 2 (its own age is 0: no drain yet)
    clock.advance(seconds=31)      # event 2 is now stale...
    admits(lim)                    # ...so this request's drain carries events 2 AND 3

    assert [len(rows) for _, rows in pool.executemany_calls] == [1, 2]


# --- ledger write SQL content ----------------------------------------------


def test_ledger_insert_sql_content_and_row_fidelity():
    pool = FakePool()
    lim = limiter(pool)
    clock = lim._clock

    admits(lim, key="viewer:alice")       # flushed by the reachability drain @t0
    clock.advance(seconds=90)
    admits(lim, key="ip:9.9.9.9")
    clock.advance(seconds=30)             # ip event hits the freshness bound...
    admits(lim, key="viewer:alice")       # ...drain carries it plus this one

    sql, _ = pool.executemany_calls[-1]
    assert sql == (
        "INSERT INTO rate_limit_events (scope_key, endpoint, occurred_at) "
        "VALUES ($1, $2, $3)"
    )
    # Flattened across batches: every admission, in arrival order, each
    # stamped with its ADMISSION time (not its flush time).
    assert all_flushed_rows(pool) == [
        ("viewer:alice", "chat", START),
        ("ip:9.9.9.9", "chat", START + timedelta(seconds=90)),
        ("viewer:alice", "chat", START + timedelta(seconds=120)),
    ]


def test_state_is_shared_across_instances_of_the_same_pool():
    """deps.py constructs a fresh RateLimiter per request; exhaustion
    must be visible across instances (and distinct pools stay isolated)."""
    pool_a, pool_b = FakePool(), FakePool()
    alice_1 = limiter(pool_a, max_requests=1, window=timedelta(hours=1))
    alice_2 = limiter(pool_a, max_requests=1, window=timedelta(hours=1))
    other = limiter(pool_b, max_requests=1, window=timedelta(hours=1))

    admits(alice_1, times=1)
    denies(alice_2)          # same pool -> same bucket -> exhausted
    admits(other, times=1)   # different pool -> independent state


def test_unlimited_endpoint_never_touches_the_pool():
    pool = FakePool()
    lim = limiter(pool)
    asyncio.run(lim.check_and_record("viewer:alice", "unknown-endpoint"))
    assert pool.acquire_count == 0


# --- flush failure -> closed door (fail-closed) -----------------------------


def test_flush_failure_closes_the_door():
    pool = FakePool(fail_executemany=True)
    lim = limiter(pool)
    admits(lim)  # admitted pre-failure; its reachability drain discovers the outage
    assert lim.door_closed is True
    exc = denies(lim)
    assert "temporarily unavailable" in str(exc)
    assert exc.retry_after_seconds == DEGRADED_RETRY_AFTER_SECONDS == 60


def test_denial_at_closed_door_consumes_no_bucket_token():
    """Infra failure must look like denial, never like free capacity:
    tokens burned while the door is closed would hand a silent budget to
    whoever retries during an outage."""
    pool = FakePool(fail_executemany=True)
    lim = limiter(pool, max_requests=2, window=timedelta(hours=1))
    clock = lim._clock

    admits(lim, times=1)   # spends token 1; its drain fails -> door closes
    denies(lim)            # closed-door denial (bucket still holds token 2)
    denies(lim)            # repeatedly: still no consumption behind the door

    pool.fail_executemany = False
    clock.advance(RETRY_GAP)
    denies(lim)            # performs the recovery replay, but THIS call stays denied
    assert lim.door_closed is False

    admits(lim, times=1)   # token 2 was never touched: it is spendable now
    denies(lim)            # and only now is the bucket truly empty


def test_saturated_buffer_denies_rather_than_growing_unbounded(monkeypatch):
    monkeypatch.setattr(governance, "LEDGER_MAX_PENDING", 3)
    monkeypatch.setattr(governance, "LEDGER_MAX_FLUSH_DELAY", timedelta(hours=1))
    pool = FakePool()
    lim = limiter(pool, max_requests=100, window=timedelta(hours=1))
    admits(lim, times=4)   # first drains via reachability; three accumulate
    assert lim.pending_count == 3
    exc = denies(lim)
    assert "saturated" in str(exc)


# --- replay-or-block --------------------------------------------------------


def test_failed_events_are_retained_for_replay_not_dropped(monkeypatch):
    """A failed drain must leave its events queued (the replay half of
    replay-or-block), even as the door closes (the block half)."""
    monkeypatch.setattr(governance, "LEDGER_FLUSH_THRESHOLD", 2)
    pool = FakePool()
    lim = limiter(pool, max_requests=100, window=timedelta(hours=1))

    admits(lim, key="viewer:alice")      # drain succeeds: ledger reachable
    pool.fail_executemany = True         # Postgres goes down
    admits(lim, key="ip:9.9.9.9")
    admits(lim, key="viewer:carol")      # crosses threshold -> drain fails here
    assert lim.door_closed is True
    assert lim.pending_count == 2        # both events retained despite the failure


def test_recovery_replays_original_events_verbatim_then_reopens():
    pool = FakePool(fail_executemany=True)
    lim = limiter(pool, max_requests=100, window=timedelta(hours=1))
    clock = lim._clock

    admits(lim, key="viewer:alice")   # admitted @START; drain fails; retained
    assert lim.door_closed

    pool.fail_executemany = False     # Postgres heals
    clock.advance(RETRY_GAP)
    exc = denies(lim)                 # this call denied, BUT it replays the ledger
    assert "temporarily unavailable" in str(exc)

    # The fake conn records failed attempts too, so both batches are
    # visible -- and verbatim replay means they must be IDENTICAL,
    # carrying the ORIGINAL admission timestamp.
    attempt_batches = [rows for _, rows in pool.executemany_calls]
    assert len(attempt_batches) == 2
    assert attempt_batches[0] == attempt_batches[1] == [
        ("viewer:alice", "chat", START),
    ], "replay must carry the ORIGINAL admission timestamp"
    assert lim.door_closed is False
    admits(lim, key="viewer:bob")     # door open again: fresh admissions flow
    assert lim.pending_count == 1


def test_degraded_retry_is_throttled_within_the_retry_interval():
    pool = FakePool(fail_executemany=True)
    lim = limiter(pool)
    clock = lim._clock

    admits(lim)                                  # first drain fails (attempt 1)
    attempts = pool.acquire_count
    clock.advance(seconds=1)                     # inside LEDGER_FLUSH_RETRY_INTERVAL
    denies(lim)
    assert pool.acquire_count == attempts, (
        "denied requests must not hammer a down Postgres on every call"
    )
    clock.advance(seconds=5)                     # interval elapsed
    denies(lim)
    assert pool.acquire_count == attempts + 1    # exactly one throttled retry


def test_events_appended_while_a_drain_is_in_flight_are_not_lost(monkeypatch):
    """The drain snapshots pending under lock; anything appended while
    the batch write is in flight must survive the success bookkeeping
    (which pops only the flushed prefix)."""
    monkeypatch.setattr(governance, "LEDGER_FLUSH_THRESHOLD", 2)
    pool = FakePool()
    lim = limiter(pool, max_requests=50, window=timedelta(hours=1))
    clock = lim._clock
    st = lim._state

    admits(lim, key="a")           # reachability drain flushes [a]
    admits(lim, key="b")           # accumulates below threshold

    def mid_flight(sql, rows):     # runs DURING executemany, before bookkeeping
        with st.lock:
            st.pending.append((clock(), "mid:flight", "chat"))

    pool.on_executemany = mid_flight
    admits(lim, key="c")           # 2 pending -> threshold crossed, drain fires
    pool.on_executemany = None
    assert lim.pending_count == 1  # the in-flight arrival survived

    clock.advance(seconds=31)      # force the survivor out via freshness
    admits(lim, key="d")
    assert all_flushed_rows(pool)[-2:] == [
        ("mid:flight", "chat", START),
        ("d", "chat", START + timedelta(seconds=31)),
    ]                              # order preserved, no duplicates, nothing dropped


# --- retention sweep ---------------------------------------------------------


def test_retention_sweep_sql_content_and_piggyback_cadence(monkeypatch):
    monkeypatch.setattr(governance, "RATE_EVENT_SWEEP_INTERVAL", timedelta(hours=1))
    pool = FakePool()
    lim = limiter(pool)
    clock = lim._clock

    def sweeps():
        return [c for c in pool.execute_calls
                if c[0] == "DELETE FROM rate_limit_events WHERE occurred_at < $1"]

    admits(lim, times=2)
    assert len(sweeps()) == 1, "first successful drain must sweep once"
    sql, params = sweeps()[0]
    assert params == (START - RATE_EVENT_RETENTION,)
    assert params[0].tzinfo is not None

    clock.advance(minutes=30)
    admits(lim, times=2)
    assert len(sweeps()) == 1, "sweeps are throttled to RATE_EVENT_SWEEP_INTERVAL"

    clock.advance(hours=1, seconds=1)
    admits(lim, times=1)
    assert len(sweeps()) == 2


def test_retention_sweep_failure_never_closes_the_door_and_never_duplicates(caplog):
    """Housekeeping is housekeeping: a failing TTL sweep degrades the
    ledger's tidiness, not enforcement. Regression guard: the sweep used
    to share the insert try-block, so its failure closed the door AND
    left good rows queued for duplicate replay."""
    import logging
    caplog.set_level(logging.ERROR)
    pool = FakePool(fail_execute=True)
    lim = limiter(pool, max_requests=50, window=timedelta(hours=1))
    clock = lim._clock

    admits(lim, times=3)
    assert lim.door_closed is False
    assert all_flushed_rows(pool) == [          # inserted exactly once each
        ("viewer:alice", "chat", START),
    ]
    clock.advance(seconds=31)
    admits(lim, times=2)
    clock.advance(seconds=31)
    admits(lim, times=1)
    # Events 2 and 3 were stamped at START (before the first advance);
    # the freshness bound then flushed them together with event 4 @+31,
    # and events 5/6 likewise. No duplicates despite failing sweeps.
    assert all_flushed_rows(pool) == [
        ("viewer:alice", "chat", START),
        ("viewer:alice", "chat", START),
        ("viewer:alice", "chat", START),
        ("viewer:alice", "chat", START + timedelta(seconds=31)),
        ("viewer:alice", "chat", START + timedelta(seconds=31)),
        ("viewer:alice", "chat", START + timedelta(seconds=62)),
    ]
    assert len(pool.execute_calls) == 1, (
        "failing sweep retries at sweep cadence, not on every drain"
    )
    assert not any("closing admission door" in r.message for r in caplog.records)


def test_purge_old_manual_entry_point_sql_and_return_value():
    pool = FakePool(execute_result="DELETE 11")
    lim = limiter(pool)
    deleted = asyncio.run(lim.purge_old(older_than=timedelta(days=3)))
    assert deleted == 11
    sql, params = pool.execute_calls[0]
    assert sql == "DELETE FROM rate_limit_events WHERE occurred_at < $1"
    assert params == (START - timedelta(days=3),)


# --- knob retunability --------------------------------------------------------


def test_named_constants_exist_and_are_module_level():
    """The knobs are consulted at call time from module globals (every
    monkeypatch above proves retunability); this pins their names."""
    for name in (
        "LEDGER_FLUSH_THRESHOLD",
        "LEDGER_MAX_PENDING",
        "LEDGER_MAX_FLUSH_DELAY",
        "LEDGER_FLUSH_RETRY_INTERVAL",
        "RATE_EVENT_RETENTION",
        "RATE_EVENT_SWEEP_INTERVAL",
        "DEGRADED_RETRY_AFTER_SECONDS",
    ):
        assert hasattr(governance, name), name
