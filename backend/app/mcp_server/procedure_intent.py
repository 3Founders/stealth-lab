"""Procedure intent: one cheap judgment, first, decides whether a prompt should open the Goal/Way picker at all.

Why: the desktop and the rooms used to search the Goal catalog for every prompt. Ordinary work -- "fix the failing
test", "add a test for the parser", "what does this function do" -- paid a find_ways call plus one or two catalog reads
and then showed a Way nobody asked for (or a "no stored procedure" card) before the model could start. The person's
question is not "does a known way exist for this kind of work" (that is find_ways' triage) but "am I asking for a
procedure to choose" -- "find procedures for research" is a question to triage, yet exactly what the picker must show
(2026-11-16).

So one classification through the semantic chain (JEV first: a single typed choice question; then the OpenAI-compatible
fallbacks) sorts the prompt into prompts.PROCEDURE_KINDS before any catalog read. Only "procedure" searches; "ordinary"
goes straight to the model, exactly as if kell held nothing.

It fails toward the search, never away from it: no judge configured, every provider failing, a reply over the time
limit, or an "ordinary" verdict below `min_confidence` all mean the picker opens as before. A skipped picker costs the
person a Way they might have used; a wasted one costs only a second or two, and "Run without a Way" is one click.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from app.mcp_server import find_ways_triage as _ft

log = logging.getLogger(__name__)

SEARCH_INTENT = "procedure"


@dataclass(frozen=True)
class ProcedureIntent:
    search: bool
    intent: Optional[str] = None        # a prompts.PROCEDURE_KINDS key, or None when no judgment was made
    confidence: Optional[float] = None
    provider: Optional[str] = None
    reason: str = ""                    # why the picker opens when it opens without a "procedure" verdict
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"search": self.search, "latency_ms": round(self.latency_ms, 1)}
        for key in ("intent", "confidence", "provider"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.reason:
            out["reason"] = self.reason
        return out


async def procedure_intent(query: str, judge: Any, *, context: str = "", timeout_s: float = 3.0,
                           min_confidence: float = 0.6) -> ProcedureIntent:
    """Classify `query` (`context`: the conversation's earlier turns, or ""). `judge` is a SemanticJudge (anything with
    `judge_procedure`); None or a judge without it means no verdict, and the picker opens."""
    method = getattr(judge, "judge_procedure", None)
    if not callable(method):
        return ProcedureIntent(True, reason="no procedure judge configured")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    try:
        result = await asyncio.wait_for(method(query, context) if context else method(query), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ProcedureIntent(True, reason=f"procedure intent over {timeout_s:g}s", latency_ms=(loop.time() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 -- the picker never fails a prompt; it just opens as before
        log.warning("procedure intent failed: %s", exc)
        return ProcedureIntent(True, reason=f"procedure intent failed: {type(exc).__name__}",
                               latency_ms=(loop.time() - t0) * 1000)
    ms = (loop.time() - t0) * 1000
    if not getattr(result, "ok", False) or not isinstance(result.value, dict):
        return ProcedureIntent(True, reason=f"no procedure verdict ({getattr(result, 'reason', '') or 'judge unavailable'})",
                               latency_ms=ms)
    intent, confidence = result.value.get("intent"), result.value.get("confidence")
    provider = getattr(result, "provider", None)
    if intent == SEARCH_INTENT:
        return ProcedureIntent(True, intent, confidence, provider, latency_ms=ms)
    if not isinstance(confidence, (int, float)) or confidence < min_confidence:
        return ProcedureIntent(True, intent, confidence, provider, reason=f"{intent} below confidence {min_confidence:g}",
                               latency_ms=ms)
    return ProcedureIntent(False, intent, confidence, provider, latency_ms=ms)


class IntentCache:
    """A short-lived memo of REAL verdicts, keyed by the prompt text and the earlier turns.

    The desktop asks once per prompt and may send it again (a retry, a queued message); the rooms ask for the same
    message the agent is about to answer. A memo makes the second ask free. Only a verdict a judge actually gave is
    kept: a timeout or an unavailable judge is never cached, so a recovered provider is used on the very next prompt."""

    def __init__(self, ttl_s: float = 120.0, max_entries: int = 512, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._max, self._clock = ttl_s, max_entries, clock
        self._items: "OrderedDict[str, tuple[float, ProcedureIntent]]" = OrderedDict()

    @staticmethod
    def _key(query: str, context: str = "") -> str:
        text = query.strip() + ("\n\0" + context.strip() if context and context.strip() else "")
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    def get(self, query: str, context: str = "") -> Optional[ProcedureIntent]:
        key = self._key(query, context)
        hit = self._items.get(key)
        if hit is None:
            return None
        if hit[0] < self._clock():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return replace(hit[1], latency_ms=0.0)

    def put(self, query: str, verdict: Optional[ProcedureIntent], context: str = "") -> None:
        if verdict is None or verdict.intent is None:
            return
        key = self._key(query, context)
        self._items[key] = (self._clock() + self._ttl, verdict)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


CACHE = IntentCache()
RATE_PER_MIN = int(os.environ.get("PROCEDURE_INTENT_RATE_PER_MIN", "60") or 60)
WINDOW = _ft.CallWindow(RATE_PER_MIN)
QUERY_MAX = _ft.QUERY_MAX
CONTEXT_MAX = _ft.CONTEXT_MAX


def default_judge() -> Any:
    """The process-wide semantic judge (JEV first), or None when this deployment has none configured."""
    try:
        from app.services import retrieval_service as _rs

        return _rs.default_judge()
    except Exception:  # noqa: BLE001 -- no judge: the picker opens as before
        return None
