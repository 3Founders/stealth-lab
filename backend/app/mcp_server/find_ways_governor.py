"""The find_ways governor: the server, not the calling model, bounds how find_ways is used.

With MCP the calling model decides when to call; models differ (DS-1000 round 4: gpt-oss never called
find_ways, deepseek called it ~2x per task) and some loop on identical tool calls. Whatever the model
does, this keeps the server in control -- per caller (MCP session, else viewer), per process:

* cache       -- an identical request (same query, same repo facts) within the window is answered from
                 the earlier reply instantly, marked `governor: {"cached": true}`;
* loop breaker -- the same request more than `identical_limit` times in the window is refused with an
                 instruction to proceed without StealthLab;
* budget      -- more than `max_calls` requests in the window are refused until the window frees up;
* minimum size -- a request under `min_words` words is answered "handle this yourself" (a fast, cheap
                 refusal beats a wasted judge round-trip).

Every refusal is a normal JSON reply (`outcome: "refused"` + `governor.reason` + `next`), never an
exception, so an agent reads it like any other answer. Pure and clock-injectable for tests.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

_WORDS = re.compile(r"\w+")


def request_key(query: str, repo_claims: str) -> str:
    norm = " ".join(_WORDS.findall((query or "").lower()))
    return hashlib.sha256(f"{norm}\x1f{(repo_claims or '').strip()}".encode()).hexdigest()


@dataclass
class _Caller:
    calls: deque = field(default_factory=deque)                 # timestamps of admitted requests
    per_key: dict = field(default_factory=dict)                 # key -> deque of timestamps
    cache: "OrderedDict[str, tuple[float, str]]" = field(default_factory=OrderedDict)


@dataclass
class Decision:
    action: str                     # "run" | "cached" | "refuse"
    reply: Optional[str] = None     # the reply to return for "cached" / "refuse"
    key: str = ""


class FindWaysGovernor:
    def __init__(self, *, window_s: int = 600, identical_limit: int = 3, max_calls: int = 30, min_words: int = 3,
                 cache_size: int = 64, max_callers: int = 5000, clock: Callable[[], float] = time.monotonic):
        self.window_s, self.identical_limit, self.max_calls = window_s, identical_limit, max_calls
        self.min_words, self.cache_size, self.max_callers, self.clock = min_words, cache_size, max_callers, clock
        self._callers: "OrderedDict[str, _Caller]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _refusal(reason: str, detail: str, nxt: str) -> str:
        return json.dumps({"outcome": "refused", "governor": {"reason": reason, "detail": detail}, "next": nxt})

    def _caller(self, caller: str) -> _Caller:
        c = self._callers.get(caller)
        if c is None:
            c = self._callers[caller] = _Caller()
            while len(self._callers) > self.max_callers:
                self._callers.popitem(last=False)
        else:
            self._callers.move_to_end(caller)
        return c

    def check(self, caller: str, query: str, repo_claims: str) -> Decision:
        now = self.clock()
        key = request_key(query, repo_claims)
        if len(_WORDS.findall(query or "")) < self.min_words:
            return Decision("refuse", self._refusal(
                "too_small", f"requests under {self.min_words} words are not worth a lookup",
                "Handle this yourself; call find_ways for a task described in a sentence or more."), key)
        with self._lock:
            c = self._caller(caller)
            cutoff = now - self.window_s
            while c.calls and c.calls[0] < cutoff:
                c.calls.popleft()
            same = c.per_key.setdefault(key, deque())
            while same and same[0] < cutoff:
                same.popleft()
            if len(same) >= self.identical_limit:
                same.append(now)
                return Decision("refuse", self._refusal(
                    "repeated_request", f"the same request {len(same)} times in {self.window_s}s",
                    "You already have this answer. Do not call find_ways again for it: proceed with what it "
                    "returned, or without StealthLab."), key)
            hit = c.cache.get(key)
            if hit is not None and hit[0] >= cutoff:
                same.append(now)
                return Decision("cached", _mark_cached(hit[1]), key)
            if len(c.calls) >= self.max_calls:
                return Decision("refuse", self._refusal(
                    "budget", f"{self.max_calls} find_ways calls per {self.window_s}s",
                    "Proceed with the knowledge you already have; find_ways is available again later."), key)
            c.calls.append(now)
            same.append(now)
            return Decision("run", key=key)

    def remember(self, caller: str, key: str, reply: str) -> None:
        """Cache a reply that is worth reusing (real answers only, never refusals or errors)."""
        if not reply.startswith("{"):
            return
        with self._lock:
            c = self._caller(caller)
            c.cache[key] = (self.clock(), reply)
            c.cache.move_to_end(key)
            while len(c.cache) > self.cache_size:
                c.cache.popitem(last=False)


def _mark_cached(reply: str) -> str:
    try:
        body = json.loads(reply)
    except ValueError:
        return reply
    if isinstance(body, dict):
        body["governor"] = {"cached": True}
        return json.dumps(body, default=str)
    return reply


_GOVERNOR: Optional[FindWaysGovernor] = None


def governor() -> Optional[FindWaysGovernor]:
    """The process-wide governor configured from settings, or None when disabled."""
    global _GOVERNOR
    from app.config import settings

    if not settings.find_ways_governor:
        return None
    if _GOVERNOR is None:
        _GOVERNOR = FindWaysGovernor(
            window_s=settings.find_ways_window_seconds, identical_limit=settings.find_ways_identical_limit,
            max_calls=settings.find_ways_max_calls_per_window, min_words=settings.find_ways_min_words)
    return _GOVERNOR
