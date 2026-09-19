"""
Sync adapter that lets a plain OpenAI-style message loop (e.g.
`app.execution.coding_agent.Agent`) use semantic compaction.

    messages = compactor.compact(messages)      # call before each model request

What it does
  * keeps the initial system/user block and the most recent messages EXACTLY
    (so tool_call/tool pairs at the boundary stay structurally valid);
  * compacts only the middle, via `compact_context`, and swaps it for ONE user
    message holding the derived view;
  * does nothing until the conversation is over the token threshold.

Safety
  * Any failure -- providers down, exception, no size win -- returns the
    messages UNCHANGED (full history retained). Never a partial/destructive
    result, never an exception into the agent loop.
  * The caller's original list is not mutated; the caller decides to adopt the
    returned list. Raw history stays wherever the harness already keeps it.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, Union

from app.services.context_compaction.engine import CompactionConfig, MemoryRetentionCache, compact_context
from app.services.context_compaction.models import StealthState, estimate_tokens
from app.services.context_compaction.normalize import items_from_chat_messages

log = logging.getLogger(__name__)

COMPACT_HEADER = ("[COMPACTED WORKING CONTEXT -- derived summary of earlier steps in this session; "
                  "exact details you still need are preserved, everything else is in durable state]")


def run_sync(coro):
    """Run a coroutine from sync code, whether or not a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _msg_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content") or "") + str(m.get("tool_calls") or "")) for m in messages)


class MessageCompactor:
    def __init__(
        self, judge_factory: Optional[Callable[[], Any]] = None,
        state: Union[StealthState, Callable[[list[dict]], StealthState], None] = None,
        cfg: Optional[CompactionConfig] = None, keep_recent_messages: int = 8,
        pool: Any = None, session_id: Optional[str] = None,
    ):
        self._judge_factory = judge_factory
        self._state = state
        self.cfg = cfg or CompactionConfig.from_settings()
        self.keep_recent = keep_recent_messages
        self._pool, self._session_id = pool, session_id
        self._cache = MemoryRetentionCache()
        self.history: list[dict] = []  # one entry per attempted compaction (stats / reason)

    def _judge(self):
        if self._judge_factory is not None:
            return self._judge_factory()
        from app.services.semantic.chain import SemanticJudge
        return SemanticJudge.from_settings()

    def _state_for(self, messages: list[dict]) -> StealthState:
        if callable(self._state):
            return self._state(messages)
        if self._state is not None:
            return self._state
        first_user = next((str(m.get("content") or "") for m in messages if m.get("role") == "user"), "")
        return StealthState(goal=first_user[:500])

    @staticmethod
    def _split(messages: list[dict], keep_recent: int) -> Optional[tuple[int, int]]:
        head_end = next((i for i, m in enumerate(messages) if m.get("role") not in ("system", "user")), None)
        if head_end is None:
            return None
        tail_start = len(messages) - keep_recent
        while tail_start > head_end and messages[tail_start].get("role") == "tool":
            tail_start -= 1  # never split an assistant tool_calls message from its tool results
        return (head_end, tail_start) if tail_start - head_end >= 2 else None

    def compact(self, messages: list[dict]) -> list[dict]:
        try:
            if _msg_tokens(messages) < self.cfg.token_threshold:
                return messages
            bounds = self._split(messages, self.keep_recent)
            if bounds is None:
                return messages
            head_end, tail_start = bounds
            middle = messages[head_end:tail_start]
            items = items_from_chat_messages(middle, id_prefix="c")
            result = run_sync(compact_context(
                items, self._state_for(messages), self._judge(), cfg=self.cfg, cache=self._cache,
                pool=self._pool, session_id=self._session_id, trigger="before_model_call", force=True))
            entry = {"status": result.status, "reason": result.reason, "stats": result.stats,
                     "retry_job_id": result.retry_job_id}
            self.history.append(entry)
            if result.status != "compacted":
                return messages  # providers down / skipped: keep the full context
            view = result.render()
            if estimate_tokens(view) >= _msg_tokens(middle):
                entry["status"] = "no_gain"
                return messages
            compact_msg = {"role": "user", "content": f"{COMPACT_HEADER}\n{view}"}
            return messages[:head_end] + [compact_msg] + messages[tail_start:]
        except Exception:  # noqa: BLE001 -- compaction must never break the agent loop
            log.warning("MessageCompactor: compaction failed; keeping full context", exc_info=True)
            self.history.append({"status": "error"})
            return messages
