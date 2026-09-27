"""Keep blocking work off the event loop.

The MCP server is one process with one event loop (`--workers 1`). A synchronous
`OpenAI(...).chat.completions.create` (or `subprocess.run`) called directly inside an
`async def` freezes that loop for the whole call -- 1-3 s per LLM call -- so every other
request on the process waits. `run_blocking` runs the call in the default thread pool
instead. If the callable turns out to be async (a test fake, or an AsyncOpenAI-shaped
client passed where a sync one was expected), the coroutine it returns is awaited here,
so callers work with either kind of client.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable


async def run_blocking(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    result = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result
