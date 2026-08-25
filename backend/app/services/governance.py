"""
Rate limiting and LLM cost governance (V2).

Two separate protections against the same class of problem, kept
separate because they fail differently:

  - **Rate limiting** bounds request *frequency*. Cheap to check, and
    the right tool against scripted abuse.

  - **Cost governance** bounds actual *spend*. A single `/v1/admin/scan`
    fans out across a debate panel, a judge, and Layer 2 simulations —
    dozens of model calls from one request. A rate limit of "10 scans an
    hour" says nothing useful about the resulting bill, which is why a
    frequency cap alone is not enough.

Both fail *closed* on infrastructure errors. A rate limiter that
silently allows everything when its backing store is unreachable is
worse than no rate limiter, because it creates false confidence.

HARDENING H3 (2026-08-26) — collector treatment for the rate limiter:

The V2 limiter enforced straight out of Postgres: every LLM-spending
request paid an advisory lock + a windowed COUNT + an INSERT. That made
the database the enforcement point AND the hottest table in the schema.
It is now the **audit ledger** only, mirroring trace_collector's
append->drain split:

  - Enforcement is an **in-process token bucket** per (scope_key,
    endpoint). The hot path performs zero I/O: check tokens, consume
    one, append the admission to a bounded in-memory pending buffer.
    Bucket state is shared per pool (a WeakKeyDictionary), so callers
    that construct a fresh RateLimiter per request — api/deps.py does —
    still share one limiter.

  - A **buffered drain** flushes pending admissions to
    rate_limit_events in one batched executemany, triggered when the
    buffer crosses LEDGER_FLUSH_THRESHOLD, when its oldest event ages
    past LEDGER_MAX_FLUSH_DELAY (keeps the ledger fresh under trickle
    traffic), or — while degraded — on the throttled retry cadence.

  - **Replay-or-block, the fail-closed contract:** if a drain fails,
    the unflushed events are RETAINED in arrival order and replayed
    verbatim (original admission timestamps) on the next successful
    drain — and the door CLOSES: every subsequent check_and_record is
    denied ("temporarily unavailable") until a drain succeeds. No
    admission is ever granted while the audit trail is known-broken;
    a request admitted just before a detected failure completes
    normally, and everything after is denied. This preserves V2's
    fail-closed-on-infra-error semantics; what changed is *when* the
    error becomes visible (at drain time rather than per check).

  - Honest, bounded loss windows (same discipline as trace_collector's
    drop_count): a hard process crash loses at most LEDGER_MAX_FLUSH_
    DELAY's worth of un-drained audit rows and resets the buckets, so
    one restart-permitting burst can exceed the pre-crash budget. The
    ledger remains a complete record of everything the limiter did NOT
    lose.

  - Multi-process decision: **no Redis.** The documented deployment is
    a single uvicorn worker (`--workers 1` is load-bearing; render.yaml
    launches one process), so per-process buckets are exact. Running
    multiple workers would multiply the effective budget per key and
    interleave one audit table — at that point Redis becomes the
    enforcement store (Postgres stays the ledger either way). That is a
    deliberate future change, not a silent assumption: this paragraph
    is the tripwire.

  - **Retention:** rate_limit_events is append-only and unbounded, so
    every successful drain piggybacks a TTL sweep deleting rows older
    than RATE_EVENT_RETENTION (throttled to once per
    RATE_EVENT_SWEEP_INTERVAL). Sweep failure is housekeeping — it is
    logged and never closes the door; only ledger WRITES can.
"""
from __future__ import annotations

import logging
import math
import threading
import weakref
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Optional, Tuple

import asyncpg

log = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class RateLimit:
    max_requests: int
    window: timedelta

    @property
    def window_seconds(self) -> int:
        return int(self.window.total_seconds())


# Defaults are deliberately conservative. These are guardrails against
# abuse and runaway cost, not capacity planning — raise them from
# observed usage rather than guessing upward.
DEFAULT_LIMITS: dict[str, RateLimit] = {
    # Expensive: fans out to a full debate panel plus judge plus Layer 2.
    "/v1/admin/scan": RateLimit(max_requests=5, window=timedelta(hours=1)),
    # Moderate: one model call plus retrieval per request.
    "/v1/chat": RateLimit(max_requests=30, window=timedelta(hours=1)),
    # Cheap, but unbounded ingestion is still a denial-of-service vector.
    "/v1/traces": RateLimit(max_requests=120, window=timedelta(hours=1)),
}


# Rough USD per million tokens. Deliberately approximate and deliberately
# rounded *up*: this drives a spending guardrail, so overestimating stops
# spend slightly early while underestimating lets it overshoot the cap.
# Local models are free, hence zero.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "anthropic": (3.0, 15.0),
    "openai": (2.5, 10.0),
    "google": (1.5, 6.0),
    "moonshot": (1.0, 3.0),
    "voyage": (0.12, 0.0),
    "local": (0.0, 0.0),
    "mock": (0.0, 0.0),
    # General Compute self-serve models, from their /models page directly
    # (checked, not estimated) -- family names match _derive_family()'s
    # output for these exact model strings. Without these, every General
    # Compute call fell through to the "unknown provider" branch below,
    # which prices at Anthropic's rate: correct direction (conservative,
    # not dangerous) but wrong enough to make the cap trigger at a small
    # fraction of real available budget.
    "gpt-oss": (0.21, 0.79),
    "deepseek": (0.21, 0.79),   # v3.1's rate; v3.2 is cheaper, this errs conservative
    "gemma": (0.16, 0.16),
    "minimax": (0.28, 1.20),
}


def estimate_cost(
    provider: str, input_tokens: int = 0, output_tokens: int = 0
) -> float:
    """
    Estimated USD for one call.

    An unknown provider is priced at the most expensive known rate rather
    than zero — an unrecognised model should not be able to spend
    invisibly just because it wasn't in the table.
    """
    key = provider.lower()
    if key in _PRICE_PER_MTOK:
        price_in, price_out = _PRICE_PER_MTOK[key]
    else:
        log.warning("unknown provider %r for cost estimation; using worst-case rate", provider)
        price_in = max(p[0] for p in _PRICE_PER_MTOK.values())
        price_out = max(p[1] for p in _PRICE_PER_MTOK.values())

    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def estimate_tokens(text: str) -> int:
    """
    Crude token estimate for when a provider doesn't return usage.

    ~4 characters per token is the standard rough heuristic for English.
    Wrong for code and non-Latin scripts, which is acceptable for a
    guardrail but would not be for billing.
    """
    return max(1, len(text) // 4)


# --- H3 collector treatment: named, monkeypatch-retunable knobs ---
# All timing/size decisions are module constants consulted at call time,
# never inlined literals — proven retunable by the offline suite.

# Pending admissions buffered before a drain is forced. The hot path
# stays SQL-free below this; one batched executemany amortizes the
# ledger write across this many requests.
LEDGER_FLUSH_THRESHOLD = 32
# Hard ceiling on unflushed events. Reaching it means drains have been
# failing for a while; admissions deny rather than grow an unauditable
# backlog. (The closed door normally prevents ever reaching this.)
LEDGER_MAX_PENDING = 4096
# Trickle-traffic freshness bound: if the OLDEST pending event is older
# than this, the next request drains, so rate_limit_events never lags
# real admissions by more than this much under low traffic.
LEDGER_MAX_FLUSH_DELAY = timedelta(seconds=30)
# While degraded (last drain failed), denied requests retry the ledger
# at most once per this interval — recovery without hammering a down
# Postgres on every denied request.
LEDGER_FLUSH_RETRY_INTERVAL = timedelta(seconds=2)
# Audit-ledger TTL. Matches purge_old's historical default.
RATE_EVENT_RETENTION = timedelta(days=2)
# Minimum spacing between retention sweeps (piggybacked on drains).
RATE_EVENT_SWEEP_INTERVAL = timedelta(hours=1)
# Retry-After for infrastructure denials. Same value V2 used for
# "temporarily unavailable", kept for continuity with existing clients.
DEGRADED_RETRY_AFTER_SECONDS = 60

_LEDGER_INSERT_SQL = (
    "INSERT INTO rate_limit_events (scope_key, endpoint, occurred_at) "
    "VALUES ($1, $2, $3)"
)
_RETENTION_DELETE_SQL = (
    "DELETE FROM rate_limit_events WHERE occurred_at < $1"
)

# One bucket set + buffer per pool for the whole process. deps.py builds
# a fresh RateLimiter per request; keying state by pool identity makes
# those instances share enforcement and buffer state (and keeps pools —
# and therefore tests using distinct fake pools — isolated from each
# other). WeakKeyDictionary so replacing a pool at shutdown doesn't leak
# limiter state.
_STATES: "weakref.WeakKeyDictionary[Any, _LimiterState]" = weakref.WeakKeyDictionary()


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Bucket:
    """Token bucket for one (scope_key, endpoint). Starts full."""
    tokens: float
    updated: datetime


@dataclass
class _LimiterState:
    """Per-pool shared state: buckets, the pending audit buffer, and the
    door. Lock ordering note: held only across synchronous sections —
    never across an await — so event-loop fairness is preserved."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    buckets: dict = field(default_factory=dict)
    # (occurred_at, scope_key, endpoint) in arrival order; replayed verbatim.
    pending: Deque[Tuple[datetime, str, str]] = field(default_factory=deque)
    last_flush_attempt: Optional[datetime] = None
    last_flush_success: Optional[datetime] = None
    last_sweep: Optional[datetime] = None
    flush_failed: bool = False
    draining: bool = False

    @property
    def door_closed(self) -> bool:
        """True while the audit trail is known-broken: a drain has failed
        and its events are still awaiting replay."""
        return self.flush_failed


class RateLimiter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        limits: Optional[dict[str, RateLimit]] = None,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._pool = pool
        self._limits = limits if limits is not None else DEFAULT_LIMITS
        self._clock = clock or _default_clock
        state = _STATES.get(pool)
        if state is None:
            state = _LimiterState()
            _STATES[pool] = state
        self._state = state

    # --- observability (used by ops/tests; cheap, lock-guarded reads) ---

    @property
    def pending_count(self) -> int:
        with self._state.lock:
            return len(self._state.pending)

    @property
    def door_closed(self) -> bool:
        return self._state.door_closed

    # --- enforcement ---

    def _bucket_for(self, scope_key: str, endpoint: str, limit: RateLimit, now: datetime) -> _Bucket:
        bucket = self._state.buckets.get((scope_key, endpoint))
        if bucket is None:
            bucket = _Bucket(tokens=float(limit.max_requests), updated=now)
            self._state.buckets[(scope_key, endpoint)] = bucket
        return bucket

    @staticmethod
    def _refill(bucket: _Bucket, limit: RateLimit, now: datetime) -> None:
        elapsed = (now - bucket.updated).total_seconds()
        if elapsed > 0:
            rate = limit.max_requests / limit.window.total_seconds()
            bucket.tokens = min(float(limit.max_requests), bucket.tokens + elapsed * rate)
        bucket.updated = now

    async def check_and_record(self, scope_key: str, endpoint: str) -> None:
        """
        Raise RateLimitExceeded if over the limit; otherwise record this
        request — in memory.

        H3: the check is a token-bucket consume (atomic — the critical
        section holds no awaits) and the record is an append to the
        pending audit buffer. Neither touches Postgres. The buffered
        drain runs opportunistically afterwards; see the module
        docstring for the fail-closed contract when THAT fails.
        """
        limit = self._limits.get(endpoint)
        if limit is None:
            return

        now = self._clock()
        st = self._state
        denial: Optional[RateLimitExceeded] = None

        with st.lock:
            bucket = self._bucket_for(scope_key, endpoint, limit, now)
            self._refill(bucket, limit, now)
            if st.flush_failed:
                # Door closed: the audit ledger last failed to accept our
                # writes. Deny WITHOUT consuming a token — failing
                # infrastructure must look like denial, never like free
                # capacity.
                denial = RateLimitExceeded(
                    "rate limiting is temporarily unavailable",
                    retry_after_seconds=DEGRADED_RETRY_AFTER_SECONDS,
                )
            elif len(st.pending) >= LEDGER_MAX_PENDING:
                denial = RateLimitExceeded(
                    "rate limit audit buffer is saturated",
                    retry_after_seconds=DEGRADED_RETRY_AFTER_SECONDS,
                )
            elif bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                st.pending.append((now, scope_key, endpoint))
            else:
                wait_seconds = (1.0 - bucket.tokens) * (
                    limit.window.total_seconds() / limit.max_requests
                )
                denial = RateLimitExceeded(
                    f"{limit.max_requests} requests per "
                    f"{limit.window_seconds // 60} minutes allowed for {endpoint}",
                    retry_after_seconds=max(1, math.ceil(wait_seconds)),
                )

        # Drain outside the lock; _drain() re-locks, decides due-ness,
        # and never raises.
        await self._drain(now)

        if denial is not None:
            raise denial

    async def _drain(self, now: datetime) -> None:
        """
        Flush pending admissions to the audit ledger in one batch, then
        piggyback the retention sweep. Never raises: success opens the
        door, failure closes it (replay retained), and callers above
        translate a closed door into denials.
        """
        st = self._state
        with st.lock:
            if st.draining or not st.pending:
                return
            oldest = st.pending[0][0]
            if st.flush_failed:
                # Degraded: recovery retries run STRICTLY on the throttle
                # cadence. The reachability/staleness clauses below must
                # not apply here — last_flush_success stays None for as
                # long as Postgres stays down, so letting them speak
                # would retry on every denied call and hammer a dead
                # store.
                due = (
                    st.last_flush_attempt is None
                    or (now - st.last_flush_attempt) >= LEDGER_FLUSH_RETRY_INTERVAL
                )
            else:
                # The very first drain of the process happens
                # unconditionally so ledger reachability is established
                # on the first request, not discovered only after
                # LEDGER_MAX_FLUSH_DELAY — an unreachable store must
                # close the door as soon as it can possibly be known,
                # matching V2's per-check fail-closed.
                freshness_due = st.last_flush_success is None or (
                    (now - oldest) >= LEDGER_MAX_FLUSH_DELAY
                )
                due = len(st.pending) >= LEDGER_FLUSH_THRESHOLD or freshness_due
            if not due:
                return
            batch = list(st.pending)
            sweep_due = st.last_sweep is None or (now - st.last_sweep) >= RATE_EVENT_SWEEP_INTERVAL
            cutoff = now - RATE_EVENT_RETENTION
            st.draining = True

        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(
                    _LEDGER_INSERT_SQL,
                    [(scope_key, endpoint, ts) for ts, scope_key, endpoint in batch],
                )
                if sweep_due:
                    # Marked before attempting so a failing sweep retries
                    # at sweep cadence, not on every drain.
                    with st.lock:
                        st.last_sweep = now
                    try:
                        await conn.execute(_RETENTION_DELETE_SQL, cutoff)
                    except Exception as exc:  # noqa: BLE001
                        # Housekeeping ONLY: a failed TTL sweep must never
                        # masquerade as a failed flush (that would close
                        # the door on good writes and replay duplicates).
                        log.warning(
                            "rate_limit_events retention sweep failed "
                            "(housekeeping only, door stays open): %s", exc,
                        )
        except Exception as exc:  # noqa: BLE001
            with st.lock:
                st.draining = False
                st.last_flush_attempt = now
                # Replay-or-block, block half: keep every event (the
                # batch list was a snapshot; pending still holds them),
                # close the door. Subsequent check_and_record calls deny
                # until a drain succeeds.
                st.flush_failed = True
            log.error("rate limit ledger flush failed, closing admission door: %s", exc)
            return

        with st.lock:
            st.draining = False
            st.last_flush_attempt = now
            st.last_flush_success = now
            st.flush_failed = False
            # Replay-or-block, replay half: drop exactly the flushed
            # prefix. Events admitted while we were draining sit behind
            # it, untouched, in arrival order.
            for _ in range(len(batch)):
                st.pending.popleft()
        log.debug("flushed %d rate limit events to the audit ledger", len(batch))

    async def purge_old(self, older_than: timedelta = RATE_EVENT_RETENTION) -> int:
        """Manual retention sweep (ops entry point; the drain path sweeps
        automatically). Returns the number of rows deleted."""
        cutoff = self._clock() - older_than
        result = await self._pool.execute(
            _RETENTION_DELETE_SQL, cutoff
        )
        return int(result.split()[-1]) if result else 0


class CostGovernor:
    """
    Bounds actual LLM spend over a rolling window.

    Checked *before* a workload starts, and recorded *after* each call.
    That ordering means a single very expensive workload can overshoot
    the cap — the check can only see spend already recorded. Reducing
    that gap would mean pre-estimating each workload's total cost, which
    is not reliably knowable for a variable-round debate. The cap is
    therefore a bound on sustained spend, not a hard per-request ceiling.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        daily_cap_usd: float = 10.0,
        per_viewer_daily_cap_usd: float = 1.0,
    ):
        self._pool = pool
        self._daily_cap = daily_cap_usd
        self._per_viewer_cap = per_viewer_daily_cap_usd

    async def spend_since(
        self, since: datetime, scope_key: Optional[str] = None
    ) -> float:
        if scope_key:
            value = await self._pool.fetchval(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM llm_spend "
                "WHERE occurred_at >= $1 AND scope_key = $2",
                since, scope_key,
            )
        else:
            value = await self._pool.fetchval(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM llm_spend "
                "WHERE occurred_at >= $1",
                since,
            )
        return float(value or 0)

    async def check_budget(self, scope_key: Optional[str] = None) -> None:
        """Raise BudgetExceeded if either the global or per-viewer cap is hit."""
        since = datetime.now(timezone.utc) - timedelta(days=1)

        try:
            total = await self.spend_since(since)
            if total >= self._daily_cap:
                raise BudgetExceeded(
                    f"daily LLM budget of ${self._daily_cap:.2f} reached "
                    f"(estimated ${total:.2f} spent in the last 24h)"
                )
            if scope_key:
                viewer_total = await self.spend_since(since, scope_key)
                if viewer_total >= self._per_viewer_cap:
                    raise BudgetExceeded(
                        f"your daily budget of ${self._per_viewer_cap:.2f} is reached "
                        f"(estimated ${viewer_total:.2f} spent in the last 24h)"
                    )
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("cost governor unavailable, denying request: %s", exc)
            raise BudgetExceeded("cost governance is temporarily unavailable") from exc

    async def record(
        self,
        provider: str,
        model: str,
        operation: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        scope_key: Optional[str] = None,
    ) -> float:
        cost = estimate_cost(provider, input_tokens, output_tokens)
        try:
            await self._pool.execute(
                "INSERT INTO llm_spend (scope_key, provider, model, operation, "
                "estimated_cost, input_tokens, output_tokens) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                scope_key, provider, model, operation, cost, input_tokens, output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            # Unlike the checks, recording fails *open*: the model call has
            # already happened and been paid for, so raising here would
            # discard completed work over a bookkeeping error. Logged
            # loudly because unrecorded spend erodes the cap's accuracy.
            log.error("failed to record LLM spend (%s/%s): %s", provider, model, exc)
        return cost
