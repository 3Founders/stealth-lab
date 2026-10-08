"""find_ways triage: one cheap judgment, first, decides whether a request needs a lookup at all.

Why: find_ways is now called for every task-like prompt whatever the model decides (the Claude Code and Cursor
hooks call it themselves), so many requests reach it that StealthLab holds nothing for -- "explain what this
function does", "rename foo to bar", "thanks, continue". Each still cost a hybrid search, several judge calls and
10-30 s. One classification through the semantic chain (JEV first: a single typed choice question; then the
OpenAI-compatible fallbacks) sorts the request into prompts.TRIAGE_KINDS before any of that runs. Only
"reusable_task" goes on to the search; every other kind gets an immediate `outcome: "not_needed"`.

It fails toward the lookup, never away from it: no judge configured, every provider failing, a reply over the
time limit, or a "skip" kind below `min_confidence` all mean the search runs as before. A skipped lookup costs
the agent knowledge it might have used; a wasted lookup only costs time. The judgment never sees repo facts
(they are not needed to tell a task from a question) and nothing about it is stored except its kind.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

RETRIEVE_KIND = "reusable_task"

_NEXT = {
    "repo_specific": "Answer from this repository: read the code it names. StealthLab holds no knowledge about it.",
    "trivial_edit": "Make the change directly; it is too small to need a known way.",
    "conversation": "Carry on with the conversation; there is nothing to look up.",
    "knowledge_question": "Answer from what you know; StealthLab stores ways of doing work, not explanations.",
}


@dataclass(frozen=True)
class Triage:
    needs_retrieval: bool
    kind: Optional[str] = None          # a prompts.TRIAGE_KINDS key, or None when no judgment was made
    confidence: Optional[float] = None
    provider: Optional[str] = None
    reason: str = ""                    # why the lookup runs when it runs without a "reusable_task" verdict
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"needs_retrieval": self.needs_retrieval, "latency_ms": round(self.latency_ms, 1)}
        for key in ("kind", "confidence", "provider"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.reason:
            out["reason"] = self.reason
        return out


async def triage(query: str, judge: Any, *, timeout_s: float = 4.0, min_confidence: float = 0.6) -> Triage:
    """Classify `query`. `judge` is a SemanticJudge (anything with `judge_triage`); None or a judge without it
    means no triage, and the lookup runs."""
    method = getattr(judge, "judge_triage", None)
    if not callable(method):
        return Triage(True, reason="no triage judge configured")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    try:
        result = await asyncio.wait_for(method(query), timeout=timeout_s)
    except asyncio.TimeoutError:
        return Triage(True, reason=f"triage over {timeout_s:g}s", latency_ms=(loop.time() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 -- triage never fails a request; the lookup just runs
        log.warning("find_ways triage failed: %s", exc)
        return Triage(True, reason=f"triage failed: {type(exc).__name__}", latency_ms=(loop.time() - t0) * 1000)
    ms = (loop.time() - t0) * 1000
    if not getattr(result, "ok", False) or not isinstance(result.value, dict):
        return Triage(True, reason=f"no triage verdict ({getattr(result, 'reason', '') or 'judge unavailable'})",
                      latency_ms=ms)
    kind, confidence = result.value.get("kind"), result.value.get("confidence")
    provider = getattr(result, "provider", None)
    if kind == RETRIEVE_KIND:
        return Triage(True, kind, confidence, provider, latency_ms=ms)
    if not isinstance(confidence, (int, float)) or confidence < min_confidence:
        return Triage(True, kind, confidence, provider, reason=f"{kind} below confidence {min_confidence:g}",
                      latency_ms=ms)
    return Triage(False, kind, confidence, provider, latency_ms=ms)


class TriageCache:
    """A short-lived memo of REAL verdicts, keyed by the request text.

    The hook asks `POST /triage` first and then, when the answer is "look it up", calls `find_ways`
    with the same text -- and find_ways triages again. This makes the second call free (no second
    judge call, no second ~1 s wait). Only a verdict a judge actually gave is kept: a timeout or an
    unavailable judge is never cached, so a recovered provider is used on the very next request."""

    def __init__(self, ttl_s: float = 120.0, max_entries: int = 512, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._max, self._clock = ttl_s, max_entries, clock
        self._items: "OrderedDict[str, tuple[float, Triage]]" = OrderedDict()

    @staticmethod
    def _key(query: str) -> str:
        return hashlib.sha256(query.strip().encode("utf-8", "replace")).hexdigest()

    def get(self, query: str) -> Optional[Triage]:
        key = self._key(query)
        hit = self._items.get(key)
        if hit is None:
            return None
        if hit[0] < self._clock():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return replace(hit[1], latency_ms=0.0)

    def put(self, query: str, verdict: Optional[Triage]) -> None:
        if verdict is None or verdict.kind is None:
            return
        key = self._key(query)
        self._items[key] = (self._clock() + self._ttl, verdict)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


class CallWindow:
    """Per-caller sliding window for the cheap `/triage` route (in memory: the route exists to avoid a
    database write). Over the limit the route answers "look it up" -- the same fail-open direction as
    every other triage failure -- and find_ways' own governor takes over."""

    def __init__(self, max_calls: int = 30, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._max, self._window, self._clock = max_calls, window_s, clock
        self._hits: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self._window:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        if len(self._hits) > 2048:                      # forget callers whose window has emptied
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self._window]:
                self._hits.pop(k, None)
        return True


CACHE = TriageCache()
RATE_PER_MIN = int(os.environ.get("FIND_WAYS_TRIAGE_RATE_PER_MIN", "30") or 30)
WINDOW = CallWindow(RATE_PER_MIN)
QUERY_MAX = 1500          # the hook sends at most this much of a prompt (find_ways cuts it the same way)


def not_needed_reply(t: Triage) -> str:
    """The reply for a request triage skipped: a normal JSON answer, like the governor's refusals."""
    return json.dumps({
        "outcome": "not_needed",
        "triage": t.as_dict(),
        "next": _NEXT.get(t.kind or "", "Handle this yourself; StealthLab has nothing to add for it.") +
                " If this IS a task others have done before, call find_ways again with the task in a full sentence.",
    })


def with_triage(reply: str, t: Optional[Triage]) -> str:
    """Record the triage verdict on a reply that ran the lookup (for measurement); text replies are unchanged."""
    if t is None:
        return reply
    try:
        body = json.loads(reply)
    except (TypeError, ValueError):
        return reply
    if not isinstance(body, dict):
        return reply
    body["triage"] = t.as_dict()
    return json.dumps(body, default=str)
