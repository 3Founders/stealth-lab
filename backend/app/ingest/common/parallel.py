"""Bounded parallelism for the ingestion pipelines.

An item spends almost all of its time waiting -- on the model, on embeddings, on database round trips -- so one item
at a time left the machine idle (2026-09-30: 77 s per verified task and ~5 min per OpenHands run against the
production database). Items are grouped into UNITS that must stay in order because they share state (all runs of one
task share its Goal and Procedures; all files of one repository share its GitHub lookups); units run in parallel,
at most `concurrency` at a time. A stop (the spend cap) lets running units finish their current item and starts
nothing new.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

DEFAULT_CONCURRENCY = 12


class Stop:
    """Set once by any unit (e.g. the spend cap); every unit checks it before its next item."""

    def __init__(self) -> None:
        self.reason: Optional[str] = None

    def set(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason

    def __bool__(self) -> bool:
        return self.reason is not None


async def run_units(units: Iterable[Sequence[Any]], handle: Callable[[Any], Awaitable[None]], *,
                    concurrency: int, stop: Stop) -> None:
    """Process each unit's items in order with `handle(item)`; up to `concurrency` units at once."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(unit: Sequence[Any]) -> None:
        async with sem:
            for item in unit:
                if stop:
                    return
                await handle(item)

    # TaskGroup, not gather: when one unit raises (a dropped connection the CLI will resume from), the others are
    # cancelled -- none keeps writing unobserved while the run restarts. The first real error is re-raised.
    try:
        async with asyncio.TaskGroup() as tg:
            for u in units:
                tg.create_task(one(u))
    except BaseExceptionGroup as group:
        raise group.exceptions[0] from None
