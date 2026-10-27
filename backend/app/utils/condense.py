"""Fit a long request into a fixed budget without losing its end.

Why: callers cut a prompt to a limit (a text search, an embedding, a judge call). A plain `text[:n]` keeps the
opening and drops the closing, but in a long prompt the actual ask is often last, after pasted logs or code. This
keeps the head and the tail and marks the gap. The result is exactly `limit` characters when the text is longer,
so existing length assertions and cache keys behave as before.

Honest scope limit: it is still a cut. The middle is lost; nothing here finds "the ask" inside the text.
The JS twin is `fitQuery` in packaging/npm/lib/hook.mjs; keep the two in step.
"""
from __future__ import annotations

MARKER = "\n...\n"
TAIL_MAX = 500
MIN_CONDENSE = 100


def condense(text: str, limit: int, tail: int = TAIL_MAX) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    tail = min(tail, max(0, limit // 4))
    head = limit - tail - len(MARKER)
    if limit < MIN_CONDENSE or head <= 0:   # too small a budget to split: a plain cut
        return text[:limit]
    return text[:head] + MARKER + (text[len(text) - tail:] if tail else "")
