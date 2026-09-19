"""
Semantic context compaction engine.

    items -> Units (tool call+result pairs) -> pins (deterministic lifecycle)
          -> batched retention judgment (semantic chain: JEV -> Gemini -> Gemma)
          -> guards (fail-safe upgrades only) -> summaries for KEEP_COMPACT
          -> derived compact view

SAFETY INVARIANTS
  * The input `items` and the canonical raw trajectory are never modified.
  * If NO semantic provider can judge, compaction is SKIPPED: the existing
    context (or the last valid compacted view + uncovered items) is retained
    and a retry is queued. There is no rule-based dropping fallback.
  * Deterministic guards may only make a decision SAFER (DROP/REFERENCE_ONLY
    -> KEEP_COMPACT, COMPACT -> VERBATIM), never more destructive.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.services.context_compaction.models import (
    PAIR_DISPOSITION, Action, CompactionResult, ContextItem, ContextRetentionDecision,
    StealthState, Unit, ViewEntry, estimate_tokens, sha,
)
from app.services.context_compaction.normalize import group_units
from app.services.context_compaction.pinning import HARD, SOFT, compute_pins
from app.services.semantic import prompts
from app.services.semantic.policy import METRICS

PROMPT_VERSION = f"{prompts.RETENTION_PROMPT_VERSION}+{prompts.SUMMARY_PROMPT_VERSION}"

TRIGGERS = ("before_model_call", "large_tool_result", "node_completed", "before_handoff",
            "before_session_continuation", "explicit")
_ALWAYS_TRIGGERS = {"node_completed", "before_handoff", "before_session_continuation", "explicit"}


@dataclass
class CompactionConfig:
    token_threshold: int = 60000
    large_result_tokens: int = 4000
    batch_size: int = 20
    min_confidence: float = 0.5
    recent_window: int = 6
    preview_chars: int = 1500
    min_units: int = 4  # never bother compacting a tiny context

    @classmethod
    def from_settings(cls, settings=None) -> "CompactionConfig":
        if settings is None:
            from app.config import settings as _s
            settings = _s
        return cls(token_threshold=settings.context_compaction_token_threshold,
                   large_result_tokens=settings.context_compaction_large_result_tokens)


def should_compact(items: list[ContextItem], trigger: str = "before_model_call",
                   cfg: Optional[CompactionConfig] = None) -> tuple[bool, str]:
    """Trigger policy -- do not compact after every tiny event."""
    cfg = cfg or CompactionConfig()
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    total = sum(i.tokens for i in items)
    if len(items) < cfg.min_units:
        return False, "context_too_small"
    if trigger in _ALWAYS_TRIGGERS:
        return True, trigger
    if trigger == "before_model_call":
        return (total >= cfg.token_threshold, f"tokens={total}>=threshold={cfg.token_threshold}")
    if trigger == "large_tool_result":
        big = items[-1].tokens >= cfg.large_result_tokens
        return (big and total >= cfg.token_threshold // 2, f"last_result_tokens={items[-1].tokens}")
    return False, "no_trigger"


# ------------------------------------------------------------------ cache


class MemoryRetentionCache:
    def __init__(self):
        self._d: dict[str, dict] = {}
        self.hits = 0

    async def get(self, key: str) -> Optional[dict]:
        v = self._d.get(key)
        if v is not None:
            self.hits += 1
        return v

    async def put(self, key: str, decision: dict) -> None:
        self._d[key] = decision


class PgRetentionCache:
    def __init__(self, pool: Any):
        self._pool = pool
        self.hits = 0

    async def get(self, key: str) -> Optional[dict]:
        row = await self._pool.fetchrow("SELECT decision FROM context_retention_cache WHERE cache_key = $1", key)
        if row is None:
            return None
        self.hits += 1
        d = row["decision"]
        return json.loads(d) if isinstance(d, str) else d

    async def put(self, key: str, decision: dict) -> None:
        await self._pool.execute(
            "INSERT INTO context_retention_cache (cache_key, decision) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (cache_key) DO NOTHING", key, json.dumps(decision))


# ---------------------------------------------------------------- helpers


def _preview(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    head = int(n * 0.65)
    return f"{text[:head]}\n...[{len(text) - n} chars elided]...\n{text[-(n - head):]}"


def _content_changed(unit: Unit, state: StealthState) -> bool:
    r = unit.result
    if r.kind != "file_read" or not r.path or not r.content_hash:
        return False
    current = state.file_hashes.get(r.path)
    return current is not None and current != r.content_hash


def _unit_payload(unit: Unit, state: StealthState, pin: Optional[tuple], cfg: CompactionConfig) -> dict:
    r = unit.result
    call = next((i for i in unit.items if i.kind == "tool_call"), None)
    refs = sorted(x for x in state.durable_ref_ids() if x and x in " ".join(i.text for i in unit.items))
    return {
        "unit_id": unit.unit_id, "kind": r.kind, "tool": r.tool_name,
        "call": call.text[:400] if call else None, "path": r.path, "tokens": unit.tokens,
        "failed": r.failed, "pinned": pin[0] if pin else None,
        "content_changed": _content_changed(unit, state), "mentioned_durable_refs": refs,
        "content": _preview(r.text, cfg.preview_chars),
    }


def _must_preserve(unit: Unit) -> list[str]:
    """Literals a summary of this unit may not lose: the read path and failing
    test ids. A summary missing them is
    rejected (unit stays verbatim) -- a structural check, not a semantic one."""
    out: list[str] = []
    r = unit.result
    if r.path:
        out.append(r.path)
    if r.failed:
        out += re.findall(r"^(?:FAILED|ERROR)\s+(\S+)", r.text, re.M)[:5]
    return out


def _render_summary(summary: Any) -> str:
    if isinstance(summary, str):
        return summary
    lines = []
    for k, v in summary.items():
        if v in (None, "", [], {}):
            continue
        lines.append(f"  {k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines)


def _verbatim_entry(unit: Unit) -> ViewEntry:
    content = "\n".join(f"[{i.item_id}:{i.kind}] {i.text}" for i in unit.items)
    return ViewEntry(unit.unit_id, [i.item_id for i in unit.items], Action.KEEP_VERBATIM, content,
                     tokens=estimate_tokens(content))


def _apply_guards(unit: Unit, d: ContextRetentionDecision, pin: Optional[tuple],
                  state: StealthState, cfg: CompactionConfig) -> ContextRetentionDecision:
    level = pin[0] if pin else None
    if level == HARD:
        d.action, d.source = Action.KEEP_VERBATIM, "pin"
        return d
    if level == SOFT and d.action in (Action.DROP, Action.KEEP_REFERENCE_ONLY):
        d.action, d.source = Action.KEEP_COMPACT, "pin"
    if d.action is Action.KEEP_REFERENCE_ONLY:
        valid = [r for r in d.durable_refs if r in state.durable_ref_ids()]
        if valid:
            d.durable_refs = valid
        else:  # a reference must point at REAL persisted state
            d.action, d.source = Action.KEEP_COMPACT, "guard"
    if d.action is Action.DROP and unit.result.failed:
        d.action, d.source = Action.KEEP_COMPACT, "guard"  # negative knowledge / loop prevention
    if d.action in (Action.DROP, Action.KEEP_REFERENCE_ONLY) and d.confidence < cfg.min_confidence:
        d.action, d.source = Action.KEEP_COMPACT, "guard"
    return d


def _decision_from_dict(d: dict) -> ContextRetentionDecision:
    return ContextRetentionDecision(
        item_id=d["unit_id"], action=Action(d["action"]), relevance=d.get("relevance", 0.5),
        reason=d.get("reason", ""), durable_refs=list(d.get("durable_refs", [])),
        confidence=d.get("confidence", 0.5))


# ------------------------------------------------------------------ main


async def compact_context(
    items: list[ContextItem], state: StealthState, judge: Any, *,
    cfg: Optional[CompactionConfig] = None, cache: Any = None, pool: Any = None,
    session_id: Optional[str] = None, execution_run_id: Optional[str] = None,
    trigger: str = "explicit", force: bool = False, enqueue_on_failure: bool = True,
) -> CompactionResult:
    """`judge` is an app.services.semantic.chain.SemanticJudge (or anything
    with the same judge_retention/summarize_context/chain_id surface)."""
    cfg = cfg or CompactionConfig()
    cache = cache if cache is not None else MemoryRetentionCache()
    start = time.monotonic()
    m = getattr(judge, "metrics", METRICS)
    units = group_units(items)
    input_tokens = sum(u.tokens for u in units)
    input_hash = sha([i.fingerprint() for i in items])

    if not force:
        go, why = should_compact(items, trigger, cfg)
        if not go:
            return CompactionResult("skipped_below_threshold", [_verbatim_entry(u) for u in units],
                                    stats={"input_tokens": input_tokens, "retained_tokens": input_tokens},
                                    reason=why)

    m.inc("compaction.invocations")
    pins = compute_pins(units, state, recent_window=cfg.recent_window)
    state_prompt, state_hash = state.for_prompt(), state.state_hash()
    chain_id = getattr(judge, "chain_id", "judge")
    version = state.goal_version or state.goal, state.node_version or state.node_id

    raw: dict[str, ContextRetentionDecision] = {}
    todo: list[tuple[Unit, str]] = []
    cache_hits = 0
    for u in units:
        key = sha("retention", u.fingerprint(), version, state_hash, chain_id, PROMPT_VERSION,
                  pins.get(u.unit_id, (None,))[0])
        hit = await cache.get(key)
        if hit is not None:
            raw[u.unit_id] = _decision_from_dict(hit)
            cache_hits += 1
        else:
            todo.append((u, key))

    provider, fallback_used, retries = None, False, 0
    for i in range(0, len(todo), cfg.batch_size):  # BATCHED: one request per batch of units
        batch = todo[i:i + cfg.batch_size]
        payloads = [_unit_payload(u, state, pins.get(u.unit_id), cfg) for u, _ in batch]
        res = await judge.judge_retention(state_prompt, payloads)
        retries += max(0, len(res.attempts) - 1)
        if not res.ok:
            return await _skip_safely(items, units, state, input_tokens, input_hash, state_hash, res.reason,
                                      pool, session_id, execution_run_id, enqueue_on_failure, trigger, start, retries, m)
        provider, fallback_used = res.provider, fallback_used or res.fallback_used
        for (u, key), d in zip(batch, res.value):
            raw[u.unit_id] = _decision_from_dict(d)
            await cache.put(key, d)

    decisions: dict[str, ContextRetentionDecision] = {}
    for u in units:
        d = _apply_guards(u, raw[u.unit_id], pins.get(u.unit_id), state, cfg)
        d.pair_disposition = PAIR_DISPOSITION[d.action] if len(u.items) > 1 else None
        decisions[u.unit_id] = d

    # ---- summaries for KEEP_COMPACT (separate call: JEV is not asked to synthesize) ----
    to_sum = [u for u in units if decisions[u.unit_id].action is Action.KEEP_COMPACT]
    summaries: dict[str, str] = {}
    summary_unavailable = 0
    for i in range(0, len(to_sum), cfg.batch_size):
        batch = to_sum[i:i + cfg.batch_size]
        res = await judge.summarize_context(
            state_prompt, [_unit_payload(u, state, pins.get(u.unit_id), cfg) for u in batch])
        retries += max(0, len(res.attempts) - 1)
        if not res.ok:
            summary_unavailable += len(batch)  # they stay VERBATIM below: context retained
            continue
        provider = provider or res.provider
        fallback_used = fallback_used or res.fallback_used
        for u in batch:
            text = _render_summary(res.value[u.unit_id])
            missing = [lit for lit in _must_preserve(u) if lit not in text]
            if missing or estimate_tokens(text) >= u.tokens:  # lost a literal / not smaller -> keep exact
                decisions[u.unit_id].action, decisions[u.unit_id].source = Action.KEEP_VERBATIM, "guard"
                continue
            summaries[u.unit_id] = text

    view: list[ViewEntry] = []
    dropped_ids: list[str] = []
    counts = {a.value: 0 for a in Action}
    dropped_tokens = 0
    for u in units:
        d = decisions[u.unit_id]
        ids = [i.item_id for i in u.items]
        if d.action is Action.KEEP_COMPACT and u.unit_id not in summaries:
            d.action = Action.KEEP_VERBATIM  # summary unavailable/rejected -> keep exact content
        counts[d.action.value] += 1
        if d.action is Action.DROP:
            dropped_ids += ids
            dropped_tokens += u.tokens
        elif d.action is Action.KEEP_VERBATIM:
            view.append(_verbatim_entry(u))
        elif d.action is Action.KEEP_COMPACT:
            c = f"[{u.unit_id}:compact{' pair=' + d.pair_disposition if d.pair_disposition else ''}]\n{summaries[u.unit_id]}"
            view.append(ViewEntry(u.unit_id, ids, d.action, c, d.durable_refs, estimate_tokens(c)))
        else:
            what = next((i.text for i in u.items if i.kind == "tool_call"), None) or f"{u.result.kind} {u.result.path or ''}".strip()
            unchanged = " (unchanged)" if u.result.kind == "file_read" and not _content_changed(u, state) else ""
            c = f"[{u.unit_id}:ref] {what[:160]} -> relevant facts persisted as {','.join(d.durable_refs)}{unchanged}"
            view.append(ViewEntry(u.unit_id, ids, d.action, c, d.durable_refs, estimate_tokens(c)))

    retained = sum(e.tokens for e in view)
    stats = {
        "input_tokens": input_tokens, "retained_tokens": retained, "dropped_tokens": dropped_tokens,
        "compression_ratio": round(retained / input_tokens, 4) if input_tokens else 1.0,
        "counts": counts, "units": len(units), "cache_hits": cache_hits, "judged_units": len(todo),
        "batches": (len(todo) + cfg.batch_size - 1) // cfg.batch_size, "retries": retries,
        "summary_unavailable": summary_unavailable, "dropped_item_ids": dropped_ids,
        "latency_ms": round((time.monotonic() - start) * 1000, 2), "trigger": trigger,
        "provider": provider, "fallback_used": fallback_used,
    }
    for k, v in counts.items():
        m.inc(f"compaction.{k.lower()}", v)
    m.inc("compaction.input_tokens", input_tokens)
    m.inc("compaction.retained_tokens", retained)
    m.inc("compaction.dropped_tokens", dropped_tokens)
    m.event("compaction", **{k: stats[k] for k in ("input_tokens", "retained_tokens", "latency_ms", "provider")})

    result = CompactionResult("compacted", view, list(decisions.values()), stats, provider, fallback_used)
    if summary_unavailable and pool is not None and enqueue_on_failure:
        result.retry_job_id = await _enqueue_retry(pool, items, state, session_id, execution_run_id, input_hash, trigger)
    await _persist_view(pool, session_id, execution_run_id, input_hash, state_hash, result, "compacted")
    return result


async def _skip_safely(items, units, state, input_tokens, input_hash, state_hash, reason, pool, session_id,
                       execution_run_id, enqueue, trigger, start, retries, m) -> CompactionResult:
    """No semantic provider could judge: RETAIN context, retry later. The view
    is the last valid compacted view plus every item it does not cover
    (verbatim) -- or simply everything verbatim."""
    m.inc("compaction.skipped_unavailable")
    prior = await load_last_valid_view(pool, session_id) if pool is not None and session_id else None
    covered: set[str] = set()
    view: list[ViewEntry] = []
    if prior:
        view += [ViewEntry(e["unit_id"], e["item_ids"], Action(e["action"]), e["content"],
                           e.get("durable_refs", []), e.get("tokens", 0)) for e in prior["entries"]]
        covered = {i for e in prior["entries"] for i in e["item_ids"]} | set(prior.get("dropped_item_ids", []))
        m.inc("compaction.context_restored_from_last_valid_view")
    for u in units:
        if not all(i.item_id in covered for i in u.items):
            view.append(_verbatim_entry(u))
    result = CompactionResult(
        "skipped_unavailable", view,
        stats={"input_tokens": input_tokens, "retained_tokens": sum(e.tokens for e in view), "retries": retries,
               "latency_ms": round((time.monotonic() - start) * 1000, 2), "trigger": trigger},
        used_last_valid_view=bool(prior), reason=reason)
    m.event("compaction_skipped", reason=reason, restored=bool(prior))
    if pool is not None and enqueue:
        result.retry_job_id = await _enqueue_retry(pool, items, state, session_id, execution_run_id, input_hash, trigger)
    await _persist_view(pool, session_id, execution_run_id, input_hash, state_hash, result, "skipped_unavailable")
    return result


# ----------------------------------------------------- persistence / retry

MAX_RETRY_PAYLOAD_CHARS = 1_000_000


async def _persist_view(pool, session_id, run_id, input_hash, state_hash, result: CompactionResult, status: str):
    if pool is None or not session_id:
        return
    view = {"entries": [{"unit_id": e.unit_id, "item_ids": e.item_ids, "action": e.action.value,
                         "content": e.content, "durable_refs": e.durable_refs, "tokens": e.tokens}
                        for e in result.view],
            "dropped_item_ids": result.stats.get("dropped_item_ids", [])}
    try:
        await pool.execute(
            "INSERT INTO context_compaction_views (session_id, execution_run_id, input_hash, state_hash, "
            "provider, model, prompt_version, status, view, stats) "
            "VALUES ($1, $2::uuid, $3, $4, $5, NULL, $6, $7, $8::jsonb, $9::jsonb)",
            session_id, run_id, input_hash, state_hash, result.provider, PROMPT_VERSION, status,
            json.dumps(view), json.dumps({k: v for k, v in result.stats.items() if k != "dropped_item_ids"}))
    except Exception:  # noqa: BLE001 -- persistence of a derived view is best-effort
        import logging
        logging.getLogger(__name__).warning("compaction: could not persist derived view", exc_info=True)


async def load_last_valid_view(pool: Any, session_id: Optional[str]) -> Optional[dict]:
    if pool is None or not session_id:
        return None
    row = await pool.fetchrow(
        "SELECT view FROM context_compaction_views WHERE session_id = $1 AND status = 'compacted' "
        "ORDER BY created_at DESC LIMIT 1", session_id)
    if row is None:
        return None
    v = row["view"]
    return json.loads(v) if isinstance(v, str) else v


def items_to_payload(items: list[ContextItem]) -> list[dict]:
    return [asdict(i) for i in items]


def items_from_payload(rows: list[dict]) -> list[ContextItem]:
    return [ContextItem(**r) for r in rows]


async def _enqueue_retry(pool, items, state, session_id, run_id, input_hash, trigger) -> Optional[int]:
    from app.services.semantic.jobs import COMPACT_CONTEXT_JOB, enqueue_semantic_job

    payload = {"session_id": session_id, "execution_run_id": run_id, "trigger": trigger,
               "items": items_to_payload(items), "state": asdict(state)}
    if len(json.dumps(payload, default=str)) > MAX_RETRY_PAYLOAD_CHARS:
        METRICS.event("compaction_requeue_skipped", reason="payload_too_large", session_id=session_id)
        return None
    try:
        return await enqueue_semantic_job(pool, COMPACT_CONTEXT_JOB, payload,
                                          dedup_key=f"compact:{session_id}:{input_hash}", delay_s=0.0)
    except Exception:  # noqa: BLE001
        return None


async def run_compaction_job(pool: Any, payload: dict, *, judge: Any = None) -> bool:
    """Queue handler body: recompact a previously-skipped context. Returns
    True on success (view persisted); False if providers are still down."""
    from app.services.semantic.chain import SemanticJudge

    judge = judge or SemanticJudge.from_settings()
    items = items_from_payload(payload["items"])
    state = StealthState(**payload["state"])
    result = await compact_context(
        items, state, judge, pool=pool, session_id=payload.get("session_id"),
        execution_run_id=payload.get("execution_run_id"), trigger=payload.get("trigger", "explicit"),
        force=True, enqueue_on_failure=False)
    return result.status == "compacted" and not result.stats.get("summary_unavailable")
