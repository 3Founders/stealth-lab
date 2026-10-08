"""Which endpoints are working right now: a small circuit breaker for the provider layer.

Why: `call_unit` used to send a call to the FIRST connection offering a unit and return FAILED when that endpoint
was down, even when another connection served the same model; and the router kept recommending a model whose
only endpoint was failing. This records, per endpoint, the failures that say "this endpoint is not working at the
moment" -- a transport error, a timeout, HTTP 408/425/429/5xx, or an account refusal (401/402/403) -- and keeps the
endpoint out of rotation for a cool-down: 30 s after the first failure, doubling to 5 min; an account refusal 15
min (someone has to fix the key or the bill). After the cool-down one call is allowed through (half-open); a
success closes the circuit. The same pattern as the semantic judge chain's `_SUSPENDED` (services/semantic/chain.py).

Scope, honestly: the state is per server process, like the judge chain's. Two instances learn about an outage
separately, and a restart forgets it. A failure that says the REQUEST was bad (400, 404, 422, a policy refusal)
is not an outage and is never recorded here -- trying another endpoint would fail the same way.

Keys: transport errors and account refusals concern the whole endpoint (connection); rate limits and server errors
are recorded per (connection, unit), because a gateway can be out of capacity for one model and fine for others.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

BASE_COOLDOWN_S = 30.0
MAX_COOLDOWN_S = 300.0
ACCOUNT_COOLDOWN_S = 900.0
RETRYABLE_STATUS = frozenset({408, 425, 429}) | frozenset(range(500, 600))
ACCOUNT_STATUS = frozenset({401, 402, 403})


def is_outage(status: Optional[int], transport: bool = False) -> bool:
    """Whether a failure says the endpoint is not working (try another), rather than that the request was bad."""
    return transport or (status is not None and (status in RETRYABLE_STATUS or status in ACCOUNT_STATUS))


LATENCY_ALPHA = 0.3                 # weight of the newest call in the moving average
SLOW_FACTOR = 2.0                   # without a configured slow_ms: slow = more than 2x the fastest alternative
SLOW_FLOOR_MS = 2000.0              # ... and over 2 s (a 300 ms vs 120 ms difference is not "slow")


@dataclass
class _State:
    failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    last_failure_at: float = 0.0


class Health:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, _State] = {}
        self._latency: dict[str, float] = {}          # "connection|unit" -> moving average of successful calls, ms

    # ---- latency: endpoints that answer, but slowly, are tried after the fast ones

    def record_latency(self, connection_id: str, unit: Optional[str], ms: Optional[float]) -> None:
        if ms is None or ms < 0:
            return
        key = f"{connection_id}|{unit}"
        with self._lock:
            prev = self._latency.get(key)
            self._latency[key] = float(ms) if prev is None else (1 - LATENCY_ALPHA) * prev + LATENCY_ALPHA * float(ms)

    def latency_ms(self, connection_id: str, unit: Optional[str]) -> Optional[float]:
        with self._lock:
            return self._latency.get(f"{connection_id}|{unit}")

    def is_slow(self, connection_id: str, unit: Optional[str], *, slow_ms: Optional[int] = None,
                best_ms: Optional[float] = None) -> bool:
        """Slow: its moving-average latency is above the endpoint's configured slow_ms, or -- without one -- more
        than SLOW_FACTOR x the fastest alternative and over SLOW_FLOOR_MS. Unknown latency is never slow."""
        ms = self.latency_ms(connection_id, unit)
        if ms is None:
            return False
        if slow_ms is not None:
            return ms > slow_ms
        return best_ms is not None and ms > max(SLOW_FLOOR_MS, SLOW_FACTOR * best_ms)

    @staticmethod
    def _keys(connection_id: str, unit: Optional[str]) -> tuple[str, Optional[str]]:
        return f"conn:{connection_id}", (f"unit:{connection_id}|{unit}" if unit else None)

    def available(self, connection_id: str, unit: Optional[str] = None) -> bool:
        now = self.clock()
        with self._lock:
            return all(self._states.get(k) is None or self._states[k].open_until <= now
                       for k in self._keys(connection_id, unit) if k)

    def record_failure(self, connection_id: str, unit: Optional[str], *, status: Optional[int] = None,
                       transport: bool = False, error: str = "") -> None:
        if not is_outage(status, transport):
            return
        whole_endpoint = transport or (status in ACCOUNT_STATUS)
        conn_key, unit_key = self._keys(connection_id, unit)
        key = conn_key if whole_endpoint or unit_key is None else unit_key
        now = self.clock()
        with self._lock:
            s = self._states.setdefault(key, _State())
            s.failures += 1
            cooldown = (ACCOUNT_COOLDOWN_S if status in ACCOUNT_STATUS
                        else min(MAX_COOLDOWN_S, BASE_COOLDOWN_S * 2 ** (s.failures - 1)))
            s.open_until, s.last_error, s.last_failure_at = now + cooldown, error[:200], now

    def record_success(self, connection_id: str, unit: Optional[str] = None) -> None:
        with self._lock:
            for k in self._keys(connection_id, unit):
                if k:
                    self._states.pop(k, None)

    def last_failure_at(self, connection_id: str, unit: Optional[str] = None) -> float:
        with self._lock:
            return max((self._states[k].last_failure_at for k in self._keys(connection_id, unit)
                        if k and k in self._states), default=0.0)

    def describe(self, connection_id: str, unit: Optional[str] = None) -> Optional[dict]:
        """{"retry_in_s", "failures", "last_error"} while the endpoint is cooling down, else None."""
        now = self.clock()
        with self._lock:
            open_states = [self._states[k] for k in self._keys(connection_id, unit)
                           if k and k in self._states and self._states[k].open_until > now]
            if not open_states:
                return None
            s = max(open_states, key=lambda x: x.open_until)
            return {"retry_in_s": round(s.open_until - now, 1), "failures": s.failures, "last_error": s.last_error}

    def reset(self) -> None:
        with self._lock:
            self._states.clear()
            self._latency.clear()


HEALTH = Health()
