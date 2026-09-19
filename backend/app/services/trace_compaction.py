"""
Trace-ingestion compaction: raw `trace_events` -> semantic compactor ->
extraction prompt (the input to Goal / Claim / Implementation / candidate-
Procedure extraction in trajectory_semantics.py).

WHY HERE
  The extraction prompt used to be the first 200 events, each field blindly
  cut to 300 chars: a long trajectory silently lost its whole tail, and a
  big file read cost as much prompt as a one-line command. This module lets
  the semantic retention judge (JEV -> Gemini -> Gemma) decide, per tool
  call+result pair, what the extractor still needs.

WHAT IS PRESERVED
  * The raw trace_events are never touched (this only shapes the PROMPT).
  * Citation integrity: every prompt index maps to the REAL trace event
    id(s) it came from (`index_refs`), so extracted objects still cite real
    evidence. A compacted line cites the events it summarizes; a DROPped unit
    has no index and therefore can never be cited.
  * Verbatim units render exactly as before (same line format / 300-char field
    cap), so untouched events look identical to the legacy prompt.

WHEN IT RUNS
  Always (when TRACE_INGESTION_COMPACTION_ENABLED): not size-gated, because short
  episodes also contain junk narration/status/background-task noise.

WHEN PROVIDERS ARE DOWN
  Compaction is skipped and context retained. Under the cap the legacy prompt
  is used unchanged. Over the cap we do NOT silently truncate the tail
  (that would be destructive dropping caused by an outage): we raise
  ExtractionTransientFailure so the existing job machinery
  (`resume_failed_extraction_jobs`) retries later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.services.context_compaction.engine import CompactionConfig, MemoryRetentionCache, PgRetentionCache, compact_context
from app.services.context_compaction.models import Action, StealthState
from app.services.context_compaction.normalize import items_from_trace_events
from app.services.procedure_extraction.schema import ExtractionTransientFailure

log = logging.getLogger(__name__)

_SHORT = {Action.KEEP_COMPACT: "compacted", Action.KEEP_REFERENCE_ONLY: "persisted"}


@dataclass
class PreparedEvents:
    lines: list[str]
    index_refs: dict[int, list[str]]            # 1-based prompt index -> raw trace_event ids
    compaction: dict = field(default_factory=dict)


class _SafePgCache:
    """Durable retention cache that can never fail an ingestion job (missing
    table, transient DB error => behave as a cache miss)."""

    def __init__(self, pool: Any):
        self._inner, self.hits = PgRetentionCache(pool), 0

    async def get(self, key):
        try:
            v = await self._inner.get(key)
            self.hits += v is not None
            return v
        except Exception:  # noqa: BLE001
            return None

    async def put(self, key, decision):
        try:
            await self._inner.put(key, decision)
        except Exception:  # noqa: BLE001
            pass


def _ingestion_cfg(settings: Any) -> CompactionConfig:
    """Offline history, not a live loop: there is no "current step" to protect,
    so NO recency-window pin (it would shield every unit of a short episode
    from ever being dropped). Unresolved failures / hard pins still apply."""
    cfg = CompactionConfig.from_settings(settings)
    cfg.recent_window = 0
    cfg.min_units = 1
    return cfg


def _legacy(events: list[dict], line_fn: Callable[[int, dict], str], cap: int, info: dict) -> PreparedEvents:
    events = events[:cap]
    return PreparedEvents(
        [line_fn(i, e) for i, e in enumerate(events, start=1)],
        {i: [str(e["id"])] for i, e in enumerate(events, start=1)}, info)


async def _durable_state(pool: Any, episode: dict, goal_text: Optional[str], claims_fn) -> StealthState:
    """Concise durable state for the retention judge: the episode goal plus
    already-persisted Claims the OWNER may see. Never `unrestricted` -- a
    private trajectory must not pull other users' private Claims into a
    prompt."""
    state = StealthState(goal=goal_text or "", run_id=None)
    if not goal_text:
        return state
    try:
        from app.services.access import AccessScope

        owner = episode.get("owner_id")
        scope = AccessScope.for_user(str(owner)) if owner else AccessScope.anonymous()
        if claims_fn is None:
            from app.services.relevant_claims import get_relevant_claims as claims_fn
        claims = await claims_fn(pool, goal=goal_text, top_k=20, access_scope=scope)
        state.claims = [{"claim_id": str(c.get("claim_id") or c.get("id")), "statement": c.get("statement")}
                        for c in claims]
    except Exception:  # noqa: BLE001 -- no durable state just means fewer REFERENCE_ONLY options
        log.warning("trace_compaction: could not load relevant claims", exc_info=True)
    return state


async def prepare_events_for_extraction(
    pool: Any, episode: dict, goal_text: Optional[str], events: list[dict], *,
    line_fn: Callable[[int, dict], str], max_events: int, judge: Any = None,
    settings: Any = None, claims_fn: Optional[Callable] = None,
) -> PreparedEvents:
    if settings is None:
        from app.config import settings as _s
        settings = _s
    legacy = _legacy(events, line_fn, max_events, {"status": "not_needed"})
    est_tokens = sum(len(x) for x in legacy.lines) // 4
    over_cap = len(events) > max_events
    if not settings.trace_ingestion_compaction_enabled:
        legacy.compaction = {"status": "disabled"}
        return legacy
    if not events:
        return legacy
    # NOT size-gated: even a short episode carries junk ("reading this file now",
    # "waiting for another agent", "launched background tasks", heartbeats) that
    # the extractor should never see. Every episode goes through the judge.

    try:
        if judge is None:
            from app.services.semantic.chain import SemanticJudge
            judge = SemanticJudge.from_settings()
        items = items_from_trace_events(events)
        ref_by_item = {i.item_id: i.raw_ref for i in items}
        state = await _durable_state(pool, episode, goal_text, claims_fn)
        result = await compact_context(
            items, state, judge, cfg=_ingestion_cfg(settings), cache=_SafePgCache(pool),
            pool=pool, session_id=episode.get("session_id"), trigger="explicit", force=True,
            enqueue_on_failure=False)  # the extraction job itself is retried by resume_failed_extraction_jobs
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_compaction: failed, retaining full context", exc_info=True)
        result, ref_by_item = None, {}
        failure = f"error: {exc!r}"
    else:
        failure = result.reason

    if result is None or result.status != "compacted":
        info = {"status": "skipped_unavailable", "reason": failure, "events": len(events), "est_tokens": est_tokens}
        if over_cap:
            raise ExtractionTransientFailure(
                f"trace compaction unavailable ({failure}); refusing to silently truncate "
                f"{len(events)} events to {max_events} -- retry when a semantic provider is reachable")
        legacy.compaction = info
        return legacy

    event_by_id = {str(e["id"]): e for e in events}
    lines: list[str] = []
    refs: dict[int, list[str]] = {}
    for entry in result.view:
        ids = list(dict.fromkeys(r for r in (ref_by_item.get(i) for i in entry.item_ids) if r))
        if not ids:
            continue
        idx = len(lines) + 1
        if entry.action is Action.KEEP_VERBATIM:
            prefix = f"{idx}. "
            bodies = [line_fn(idx, event_by_id[i]) for i in ids]      # each is "<idx>. <body>"
            lines.append(prefix + " | ".join(b[len(prefix):] if b.startswith(prefix) else b for b in bodies))
        else:
            lines.append(f"{idx}. [{_SHORT.get(entry.action, 'compacted')}] " + entry.content.replace("\n", " "))
        refs[idx] = ids
    truncated = len(lines) > max_events
    if truncated:  # pre-existing hard safety net; upstream segmentation is the real bound
        log.warning("trace_compaction: %d lines after compaction exceed cap %d; truncating", len(lines), max_events)
        lines, refs = lines[:max_events], {i: r for i, r in refs.items() if i <= max_events}
    info = {**{k: result.stats.get(k) for k in ("input_tokens", "retained_tokens", "dropped_tokens", "compression_ratio",
                                               "counts", "provider", "fallback_used", "latency_ms")},
            "status": "compacted", "events_in": len(events), "units_out": len(lines),
            "truncated_after_compaction": truncated}
    return PreparedEvents(lines, refs, info)
