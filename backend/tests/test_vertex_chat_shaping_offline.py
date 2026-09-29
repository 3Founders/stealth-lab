"""Offline: small-budget Gemini 3.x calls get headroom for their hidden reasoning, and nothing else is touched.

Measured 2026-09-29 on google/gemini-3.8-flash: max_tokens=64 at the default effort returned content=None (the reasoning ate
the whole budget), which the claim-equivalence classifier (max_tokens=60) logged as "classification call failed,
abstaining" on every run."""
from __future__ import annotations

import pytest

from app.services import ingestion_jobs
from app.services.vertex_chat import SMALL_BUDGET_TOKENS, THINKING_HEADROOM_TOKENS, is_thinking_model, shape_chat_kwargs

GEMINI3 = "google/gemini-3.8-flash"


def test_a_small_budget_gets_headroom_and_low_reasoning_effort():
    out = shape_chat_kwargs(GEMINI3, {"max_tokens": 60, "temperature": 0})
    assert out["max_tokens"] == 60 + THINKING_HEADROOM_TOKENS
    assert out["reasoning_effort"] == "low" and out["temperature"] == 0


def test_a_large_extraction_budget_is_left_exactly_as_it_was():
    """Changing the reasoning effort of the 16,000-token extraction calls could change extraction quality."""
    kwargs = {"max_tokens": 16000, "temperature": 0.1}
    assert shape_chat_kwargs(GEMINI3, kwargs) == kwargs
    assert shape_chat_kwargs(GEMINI3, {"max_tokens": SMALL_BUDGET_TOKENS}) == {"max_tokens": SMALL_BUDGET_TOKENS}


def test_an_explicit_reasoning_effort_is_never_overridden():
    out = shape_chat_kwargs(GEMINI3, {"max_tokens": 60, "reasoning_effort": "high"})
    assert out["reasoning_effort"] == "high" and out["max_tokens"] == 60 + THINKING_HEADROOM_TOKENS


def test_max_completion_tokens_is_handled_too():
    out = shape_chat_kwargs(GEMINI3, {"max_completion_tokens": 100})
    assert out["max_completion_tokens"] == 100 + THINKING_HEADROOM_TOKENS and out["reasoning_effort"] == "low"


@pytest.mark.parametrize("model", ["google/gemini-2.5-flash", "gemma-4-31B-it", "", None])
def test_other_models_are_untouched(model):
    kwargs = {"max_tokens": 60}
    assert shape_chat_kwargs(model, kwargs) == kwargs


def test_a_call_with_no_token_cap_is_untouched():
    assert shape_chat_kwargs(GEMINI3, {"temperature": 0}) == {"temperature": 0}


def test_the_callers_dict_is_never_mutated():
    kwargs = {"max_tokens": 60}
    shape_chat_kwargs(GEMINI3, kwargs)
    assert kwargs == {"max_tokens": 60}


def test_is_thinking_model():
    assert is_thinking_model("google/gemini-3.8-flash") and is_thinking_model("gemini-3.1-pro")
    assert not is_thinking_model("google/gemini-2.5-flash")


def test_the_vertex_client_applies_it_and_still_swaps_in_our_model_name():
    """The wiring: _VertexOAuthCompletions.create is the one place every Vertex chat call passes through."""
    seen = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            return "ok"

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    completions = ingestion_jobs._VertexOAuthCompletions(_Client(), GEMINI3)
    assert completions.create(model="whatever-the-caller-asked-for", max_tokens=60, messages=[]) == "ok"
    assert seen["model"] == GEMINI3, "the configured model name still wins"
    assert seen["max_tokens"] == 60 + THINKING_HEADROOM_TOKENS and seen["reasoning_effort"] == "low"
