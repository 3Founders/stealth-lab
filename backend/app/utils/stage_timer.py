"""Per-request stage timing: where the time of ONE request went.

`find_ways` is a pipeline of stages (triage judgment, embedding, two search legs, judge calls, hierarchy
expansion, one Procedure tier per Goal node, the repo-facts judge, the model plan). Before this there was one
number per request (`total_ms`) and no way to say which stage to optimise: the latency work that followed was
estimates. This module records each stage's wall time into a recorder that lives in a `ContextVar`, so any code on
the request's path can time itself without a recorder being threaded through every signature:

    rec = stage_timer.begin()                  # once, at the top of the request
    with stage_timer.stage("embed"):           # anywhere below it, sync or `await` inside
        vector = await embedder.embed_one(q)
    stage_timer.add("judge_goal", ms)          # when the code already measured the time itself
    rec.as_dict()                              # {"embed": {"ms": 412.0, "n": 1, "max_ms": 412.0}, ...}

Properties that matter:
  * Free when nobody is listening. Without a recorder (every other tool, a script, a test) `stage` and `add` do a
    ContextVar read and return.
  * Concurrent work is counted once per call, so a stage's `ms` is the SUM over its calls (`n` of them) and can add
    up to more than the request's wall time when calls overlap; `max_ms` is the longest single call. Compare stages
    with each other and with `total_ms`, not as a partition of it.
  * Tasks started with `asyncio.gather`/`create_task` and threads started with `asyncio.to_thread` inherit the
    context, so they write into the same recorder.
  * Stage names are fixed strings chosen by the instrumented code (never a query, id or any request text), so a
    recorded breakdown carries no user data.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_RECORDER: ContextVar[Optional["StageRecorder"]] = ContextVar("stage_recorder", default=None)


class StageRecorder:
    def __init__(self) -> None:
        self._stages: dict[str, list[float]] = {}     # name -> [total_ms, calls, max_ms]
        self._t0 = time.perf_counter()

    def add(self, name: str, ms: float) -> None:
        entry = self._stages.setdefault(name, [0.0, 0, 0.0])
        entry[0] += ms
        entry[1] += 1
        if ms > entry[2]:
            entry[2] = ms

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def as_dict(self) -> dict[str, dict]:
        return {name: {"ms": round(total, 1), "n": calls, "max_ms": round(longest, 1)}
                for name, (total, calls, longest) in self._stages.items()}


def begin() -> StageRecorder:
    """Start recording for the current request (replacing any recorder already in this context)."""
    recorder = StageRecorder()
    _RECORDER.set(recorder)
    return recorder


def current() -> Optional[StageRecorder]:
    return _RECORDER.get()


def add(name: str, ms: float) -> None:
    """Record a duration the caller measured itself."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.add(name, ms)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time the block under `name`. A block that raises is still timed (the time was spent)."""
    recorder = _RECORDER.get()
    if recorder is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        recorder.add(name, (time.perf_counter() - t0) * 1000.0)


def snapshot() -> dict[str, dict]:
    """The current request's stages, or {} when none is being recorded."""
    recorder = _RECORDER.get()
    return recorder.as_dict() if recorder is not None else {}
