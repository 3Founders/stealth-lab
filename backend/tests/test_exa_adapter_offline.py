"""Exa through kell's own key, and a Way tool step's agent loop (several tool calls per round). Offline: the adapter's
listing is real; TOOL_CALL/TOOL_LIST and the model are fakes."""
import asyncio
import json

import pytest

from app import tool_providers as tp
from app.submissions import runs


def test_exa_is_an_adapter_under_its_names_and_urls():
    for name in ("exa", "ai.exa/exa", "registry:ai.exa/exa", "https://mcp.exa.ai/mcp", "https://mcp.exa.ai", "mcp.exa.ai"):
        assert tp.get(name) is not None, name
    assert tp.adapter_only("exa") is not None


def test_a_provider_tool_name_resolves_to_its_adapter():
    module, tool = tp.adapter_for_tool("web_search_exa")
    assert module is tp.get("exa")
    assert tool == "web_search"
    assert tp.adapter_for_tool("web_fetch_exa")[1] == "web_fetch"


def test_tool_candidates_never_splits_an_mcp_url():
    assert runs.tool_candidates("https://mcp.exa.ai/mcp") == [("https://mcp.exa.ai/mcp", "")]
    first = runs.tool_candidates("web_search_exa")[0]
    assert first[1] == "web_search"
    assert first[0] in tp.get("exa").SERVERS


def test_exa_lists_its_tools_in_mcp_shape():
    tools = {t["name"]: t for t in tp.get("exa").list_tools()}
    assert set(tools) == {"web_search", "web_fetch"}
    assert tools["web_search"]["inputSchema"]["required"] == ["query"]
    assert tools["web_fetch"]["inputSchema"]["properties"]["urls"]["type"] == "array"


def _run(step, run, *, list_reply=None, calls=None, model_replies=None):
    seen = []

    async def tool_list(owner, server):
        return list_reply or json.dumps({"server": server, "tools": [
            {"name": "web_search", "description": "search", "input_schema": {"type": "object"}},
            {"name": "web_fetch", "description": "read", "input_schema": {"type": "object"}}]})

    async def tool_call(owner, server, name, arguments):
        seen.append((name, dict(arguments)))
        if calls and name in calls:
            return calls[name]
        return f"{name} result for {arguments}"

    async def model(system, user, **kw):
        return model_replies.pop(0)

    old = (runs.TOOL_LIST, runs.TOOL_CALL, runs._model)
    runs.TOOL_LIST, runs.TOOL_CALL, runs._model = tool_list, tool_call, model
    try:
        out = asyncio.run(runs._run_tool(step, run))
    finally:
        runs.TOOL_LIST, runs.TOOL_CALL, runs._model = old
    return out, seen


STEP = {"locator": {"value": "exa"}, "prompt": "find benchmarks", "title": "Search"}
RUN = {"owner_id": "u", "input": "research", "outputs": []}


def test_a_tool_step_makes_several_calls_in_one_round_and_writes_the_output():
    out, seen = _run(STEP, RUN, model_replies=[
        json.dumps({"tool_calls": [
            {"tool": "web_search", "arguments": {"query": "benchmarks"}},
            {"tool": "web_search", "arguments": {"query": "baselines"}}]}),
        json.dumps({"output": "Two searches found the benchmarks."}),
    ])
    assert [name for name, _ in seen] == ["web_search", "web_search"]
    assert out == "Two searches found the benchmarks."


def test_a_tool_step_can_use_a_second_round_then_synthesize():
    out, seen = _run(STEP, RUN, model_replies=[
        json.dumps({"tool_calls": [{"tool": "web_search", "arguments": {"query": "a"}}]}),
        json.dumps({"tool_calls": [{"tool": "web_fetch", "arguments": {"url": "https://x"}}]}),
        json.dumps({"output": "Read the page."}),
    ])
    assert [name for name, _ in seen] == ["web_search", "web_fetch"]
    assert out == "Read the page."


def test_a_consent_link_pauses_the_step():
    with pytest.raises(runs.NeedsPerson) as err:
        _run(STEP, RUN,
             calls={"web_search": json.dumps({"consent_required": True, "server": "exa",
                                              "link": "https://kell/allow"})},
             model_replies=[json.dumps({"tool_calls": [{"tool": "web_search", "arguments": {"query": "a"}}]})])
    assert err.value.waiting["kind"] == "signin"
