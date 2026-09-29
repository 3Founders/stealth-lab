"""Request shaping for Gemini 3.x chat calls on Vertex (OpenAI-compatible endpoint).

THE FAILURE THIS FIXES
    Gemini 3.x reasons before it answers, and `max_tokens` counts the reasoning tokens. Measured 2026-09-29 on
    `google/gemini-3.8-flash` at the default reasoning effort:

        max_tokens=64                          -> choices[0].message.content is None (all 64 tokens were reasoning)
        max_tokens=64,  reasoning_effort=low   -> "same" (58 reasoning tokens + 1 answer token: still nearly all of it)
        max_tokens=512, default effort         -> "same" (259 reasoning tokens)

    So every small-budget call -- the claim-equivalence classifier (max_tokens=60), identity and goal judges -- returned an
    empty reply and was logged as "classification call failed, abstaining", silently weakening duplicate detection on
    every ingestion run. The ledger also under-counts the hidden reasoning tokens (it reads `completion_tokens`).

WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT
    Only for Gemini 3.x models and only for SMALL budgets (< SMALL_BUDGET_TOKENS): reasoning effort is set to "low" unless
    the caller chose one, and THINKING_HEADROOM_TOKENS are added to the cap (a cap, not a charge: unused headroom costs
    nothing). Large extraction calls (16,000 tokens) are left alone -- changing their reasoning effort could change
    extraction quality, which is a measured decision, not a side effect of this fix.
"""
from __future__ import annotations

from typing import Any

SMALL_BUDGET_TOKENS = 4096
THINKING_HEADROOM_TOKENS = 1024
_TOKEN_KEYS = ("max_tokens", "max_completion_tokens")


def is_thinking_model(model: str) -> bool:
    return "gemini-3" in (model or "").lower()


def shape_chat_kwargs(model: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """A copy of `kwargs` safe to send: never mutates the caller's dict, never overrides an explicit choice."""
    out = dict(kwargs)
    if not is_thinking_model(model):
        return out
    small = False
    for key in _TOKEN_KEYS:
        value = out.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value < SMALL_BUDGET_TOKENS:
            out[key] = value + THINKING_HEADROOM_TOKENS
            small = True
    if small:
        out.setdefault("reasoning_effort", "low")
    return out
