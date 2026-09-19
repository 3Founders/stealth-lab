"""Retry policy + in-process metrics for the semantic-judge chain."""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class RetryPolicy:
    per_provider_attempts: int = 2
    job_max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    timeout_s: float = 15.0
    requeue_delay_s: float = 60.0
    # Wall-clock cap for one chain run. Interactive (MCP) callers use a short
    # one so a tool call never blocks; background jobs pass None.
    deadline_s: Optional[float] = None

    def backoff(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        """Bounded exponential backoff with jitter (50-100% of the step)."""
        step = min(self.backoff_max_s, self.backoff_base_s * (2 ** max(0, attempt - 1)))
        return step * (0.5 + 0.5 * rng())

    def requeue_delay(self, round_no: int, rng: Callable[[], float] = random.random) -> float:
        step = min(self.requeue_delay_s * (2 ** max(0, round_no - 1)), self.requeue_delay_s * 16)
        return step * (0.5 + 0.5 * rng())

    def interactive(self, deadline_s: float = 20.0) -> "RetryPolicy":
        return RetryPolicy(**{**self.__dict__, "deadline_s": deadline_s})


def policy_from_settings(settings=None) -> RetryPolicy:
    if settings is None:
        from app.config import settings as _s
        settings = _s
    return RetryPolicy(
        per_provider_attempts=max(1, settings.semantic_provider_retries),
        job_max_retries=max(0, settings.semantic_job_max_retries),
        backoff_base_s=settings.semantic_backoff_base_ms / 1000.0,
        backoff_max_s=settings.semantic_backoff_max_ms / 1000.0,
        timeout_s=settings.semantic_provider_timeout_ms / 1000.0,
        requeue_delay_s=float(settings.semantic_requeue_delay_seconds),
    )


@dataclass
class SemanticMetrics:
    """Counters returned/inspected by callers -- this repo has no telemetry
    backend (see ObservabilityCounters in claim_conditioned_retrieval), so
    this is a plain in-process object plus an event log."""
    counters: Counter = field(default_factory=Counter)
    events: list = field(default_factory=list)
    max_events: int = 500

    def inc(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    def event(self, name: str, /, **fields) -> None:
        self.events.append({"event": name, **fields})
        del self.events[: -self.max_events]

    def snapshot(self) -> dict:
        return {"counters": dict(self.counters), "events": list(self.events)}

    def reset(self) -> None:
        self.counters.clear()
        self.events.clear()


METRICS = SemanticMetrics()
