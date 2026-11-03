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


def test_a_model_that_cannot_answer_falls_back_to_the_next_one(monkeypatch):
    monkeypatch.setenv("KELL_ROOM_AGENT_MODEL", "main")
    monkeypatch.setenv("KELL_ROOM_AGENT_FALLBACKS", "second,third")
    assert sv._room_agent_model_chain() == ["main", "second", "third"]
    tried = []

    async def one(authorization, messages, tools, model):
        tried.append(model)
        if model != "second":
            raise RuntimeError("Credit balance is empty")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(sv, "_room_agent_model_one", one)
    out = asyncio.run(sv._room_agent_model("Bearer t", [], []))
    assert out["choices"][0]["message"]["content"] == "ok" and tried == ["main", "second"]


def test_the_stream_falls_back_only_before_anything_was_sent(monkeypatch):
    monkeypatch.setenv("KELL_ROOM_AGENT_MODEL", "main")
    monkeypatch.setenv("KELL_ROOM_AGENT_FALLBACKS", "second")

    async def one(authorization, messages, tools, model):
        if model == "main":
            raise RuntimeError("402")
        yield {"choices": [{"delta": {"content": "hi"}}]}

    monkeypatch.setattr(sv, "_room_agent_model_stream_one", one)

    async def run():
        return [c async for c in sv._room_agent_model_stream("Bearer t", [], [])]

    assert asyncio.run(run())[0]["choices"][0]["delta"]["content"] == "hi"


def test_a_server_that_answers_401_is_not_offered_as_runnable_again():
    from app.tool_routing import gateway as g
    from app.tool_routing.catalog import ToolCard

    c = ToolCard(id="registry:com.pdfoven/p", source="registry", name="com.pdfoven/p", title="", description="", server="com.pdfoven/p",
                 extra={"connector_url": "https://pdfoven.com/mcp", "probe": "live", "verified": True, "owner_count": 1, "stars": 0})
    g.NEEDS_SIGNIN.discard("https://pdfoven.com/mcp")
    assert g.gateway_allowed(c)
    g.NEEDS_SIGNIN.add("https://pdfoven.com/mcp")
    try:
        assert not g.gateway_allowed(c)
    finally:
        g.NEEDS_SIGNIN.discard("https://pdfoven.com/mcp")


def test_a_model_that_failed_for_lack_of_credit_is_skipped_for_a_while(monkeypatch):
    monkeypatch.setenv("KELL_ROOM_AGENT_MODEL", "gemma-4-31b-it")
    monkeypatch.setenv("KELL_DEFAULT_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("KELL_ROOM_AGENT_FALLBACKS", raising=False)
    sv._MODEL_DOWN.clear()
    assert sv._room_agent_model_chain() == ["gemma-4-31b-it", "glm-5.3-flash"]
    sv._mark_dead("gemma-4-31b-it", RuntimeError("general-compute: HTTP 402: Credit balance is empty"))
    assert sv._room_agent_model_chain() == ["glm-5.3-flash"]
    sv._mark_dead("glm-5.3-flash", RuntimeError("insufficient credits"))
    assert sv._room_agent_model_chain() == ["gemma-4-31b-it", "glm-5.3-flash"]          # every model dead: all are tried again
    sv._MODEL_DOWN.clear()
    sv._mark_dead("gemma-4-31b-it", RuntimeError("the model returned a 500"))             # an ordinary failure does not blacklist it
    assert "gemma-4-31b-it" in sv._room_agent_model_chain()


def test_the_production_glm_connection_leaves_room_for_the_answer():
    import json
    import pathlib

    cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "deploy" / "provider_connections.json").read_text(encoding="utf-8"))
    glm = next(c for c in cfg["connections"] if c["connection_id"] == "zai-glm")
    assert glm["request_extras"] == {"reasoning_effort": "low"}       # glm-5.3-flash always thinks; at the default effort it used 6,000 tokens before answering
