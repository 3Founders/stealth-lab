"""The room agent's call_model runs on the cheapest model the person can use (app/mcp_server/server.py _cheapest_model), and the first round of a
task is a forced discover_tools call (_tool_choice). No network: the model list is faked."""
from __future__ import annotations

import asyncio

import pytest

from app.mcp_server import server as sv


def _m(model, a, b):
    return {"id": model, "stealthlab": {"input_per_mtok": a, "output_per_mtok": b}}


def test_one_offered_tool_is_forced_and_several_are_left_to_the_model():
    one = [{"type": "function", "function": {"name": "discover_tools"}}]
    assert sv._tool_choice(one) == {"type": "function", "function": {"name": "discover_tools"}}
    assert sv._tool_choice(one + one) == "auto" and sv._tool_choice([]) == "auto"


def test_the_cheapest_priced_model_is_chosen_and_remembered(monkeypatch):
    calls = []

    async def fake_list(scope):
        calls.append(1)
        return [_m("big", 3.0, 15.0), _m("small", 0.1, 0.4), {"id": "unpriced"}, _m("mid", 0.5, 2.0)]

    monkeypatch.setattr("app.providers.chat.list_models", fake_list)
    monkeypatch.setattr(sv, "_caller_access_scope", lambda: object())
    monkeypatch.delenv("KELL_CHEAP_MODEL", raising=False)
    sv._CHEAP.update(at=0.0, model="")
    assert asyncio.run(sv._cheapest_model("u1")) == "small"
    assert asyncio.run(sv._cheapest_model("u1")) == "small" and len(calls) == 1          # remembered for a while


def test_with_no_prices_the_default_runs_and_an_override_wins(monkeypatch):
    async def fake_list(scope):
        return [{"id": "a"}, {"id": "b"}]

    monkeypatch.setattr("app.providers.chat.list_models", fake_list)
    monkeypatch.setattr(sv, "_caller_access_scope", lambda: object())
    monkeypatch.delenv("KELL_CHEAP_MODEL", raising=False)
    monkeypatch.setenv("KELL_DEFAULT_MODEL", "glm-5.3-flash")
    sv._CHEAP.update(at=0.0, model="")
    assert asyncio.run(sv._cheapest_model("u1")) == "glm-5.3-flash"
    monkeypatch.setenv("KELL_CHEAP_MODEL", "my-cheap")
    assert asyncio.run(sv._cheapest_model("u1")) == "my-cheap"
