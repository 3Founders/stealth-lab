"""
Live feed of the routing tool calls this MCP server handles (find_ways, call_model, report_result, use_tool), so the
local console can show what the router told the agent as it happens.

Scope limits, stated plainly: the feed lives in this process's memory only (a bounded ring, nothing is written to
disk or the database), it is gone when the server restarts, and it is served only to a loopback caller in
single-user mode (server.py's ``_is_local_request``). It holds the tool's own return text, so it is never exposed on a
shared deployment. Argument values are cut short; the output is cut at ``MAX_OUTPUT`` characters and says so.
"""
from __future__ import annotations

import asyncio
import functools
import itertools
import json
import time
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable

MAX_EVENTS = 200
MAX_OUTPUT = 20_000
MAX_ARG = 300

_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_subscribers: set[asyncio.Queue] = set()
_ids = itertools.count(1)


def _short_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k == "ctx" or v is None:  # unset optional arguments are noise
            continue
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        out[k] = text if len(text) <= MAX_ARG else text[:MAX_ARG] + f"… (+{len(text) - MAX_ARG} chars)"
    return out


def record(tool: str, args: dict[str, Any], output: str, ms: int, ok: bool) -> dict[str, Any]:
    truncated = len(output) > MAX_OUTPUT
    ev = {"id": next(_ids), "ts": time.time(), "tool": tool, "args": _short_args(args), "ok": ok, "ms": ms,
          "output": output[:MAX_OUTPUT], "truncated": truncated}
    _events.append(ev)
    for q in list(_subscribers):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:  # a stalled viewer loses events, never slows a tool call
            pass
    return ev


def traced(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Wrap an async tool so its call and result are recorded. ``functools.wraps`` keeps the signature the MCP
    schema is built from. A failure is recorded and re-raised unchanged."""
    @functools.wraps(fn)
    async def wrapper(*a: Any, **kw: Any) -> Any:
        t0 = time.monotonic()
        try:
            result = await fn(*a, **kw)
        except Exception as exc:
            record(fn.__name__, kw, f"{type(exc).__name__}: {exc}", int((time.monotonic() - t0) * 1000), False)
            raise
        record(fn.__name__, kw, result if isinstance(result, str) else json.dumps(result, default=str),
               int((time.monotonic() - t0) * 1000), True)
        return result
    return wrapper


def recent(after: int = 0) -> list[dict[str, Any]]:
    return [e for e in _events if e["id"] > after]


async def stream(after: int = 0, heartbeat_s: float = 15.0) -> AsyncIterator[str]:
    """SSE text: what is already recorded after ``after``, then each new event; a comment line keeps idle links open."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    try:
        last = after
        for ev in recent(after):
            last = ev["id"]
            yield f"id: {ev['id']}\ndata: {json.dumps(ev)}\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if ev["id"] > last:
                last = ev["id"]
                yield f"id: {ev['id']}\ndata: {json.dumps(ev)}\n\n"
    finally:
        _subscribers.discard(q)
