"""
Progressive tool discovery on the MCP server (settings.mcp_tool_discovery): only the core tools are listed;
discover_tools finds the others with their arguments; use_tool runs one with the same checks as a direct call;
hidden tools stay callable by name. Offline: call_unit is a fake.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import app.mcp_server.server as srv
import app.providers as providers
from app.providers.types import CallResult
from app.services.access import AccessScope

SCOPE = AccessScope.for_org_member("u1", ["org-a"])


def run(coro):
    return asyncio.run(coro)


def _listed():
    return sorted(t.name for t in run(srv.server.list_tools()))


def test_progressive_lists_only_the_core(monkeypatch):
    monkeypatch.setattr(srv.settings, "mcp_tool_discovery", "progressive")
    assert _listed() == ["discover_tools", "find_ways", "report_result", "use_tool"]


def test_all_lists_every_tool_without_the_discovery_pair(monkeypatch):
    monkeypatch.setattr(srv.settings, "mcp_tool_discovery", "all")
    assert _listed() == ["call_model", "find_ways", "recommend_models", "report_discovery", "report_model_run",
                         "report_result", "submit_way"]


def test_hidden_tools_stay_registered_so_direct_callers_keep_working():
    registered = {t.name for t in srv.server._tool_manager.list_tools()}
    assert {"call_model", "submit_way", "report_model_run", "recommend_models", "report_discovery"} <= registered


def test_the_catalog_is_short_and_points_at_the_next_step():
    body = json.loads(run(srv.discover_tools(None)))
    names = {t["name"] for t in body["tools"]}
    assert names == {"call_model", "recommend_models", "report_discovery", "report_model_run", "submit_way"}
    assert all("arguments" not in t for t in body["tools"])                 # names and summaries only
    assert "use_tool" in body["next"]


@pytest.mark.parametrize("need,first", [
    ("run this sub-task on a cheaper model", "call_model"),
    ("propose a new way for this goal", "submit_way"),
    ("report a fix I made to a procedure", "report_discovery"),
])
def test_a_need_finds_the_right_tool_with_its_arguments(need, first):
    body = json.loads(run(srv.discover_tools(None, need=need)))
    top = body["tools"][0]
    assert top["name"] == first
    assert top["arguments"]["type"] == "object" and "properties" in top["arguments"]
    assert top["call_with"].startswith(f"use_tool(name={first!r}")


def test_names_give_full_entries_and_report_unknown_ones():
    body = json.loads(run(srv.discover_tools(None, names=["call_model", "nope"])))
    assert [t["name"] for t in body["tools"]] == ["call_model"] and body["unknown"] == ["nope"]
    assert "fallback_models" in body["tools"][0]["arguments"]["properties"]


def test_use_tool_runs_the_tool_with_its_own_checks(monkeypatch):
    seen = {}

    async def fake_call(pool, scope, unit, request, **kw):
        seen["unit"] = unit
        return CallResult(unit=unit, connection_id="c", text="hi back")
    monkeypatch.setattr(providers, "call_unit", fake_call)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": None}))
    out = json.loads(run(srv.use_tool(ctx, name="call_model", arguments={"prompt": "hi", "model": "m"})))
    assert out["text"] == "hi back" and seen["unit"] == "m|direct"


def test_use_tool_enforces_the_target_tools_scope(monkeypatch):
    token = SimpleNamespace(scopes=["stealthlab:tools", srv._READ], subject="u1", client_id="x")
    monkeypatch.setattr(srv, "get_access_token", lambda: token)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": None}))
    out = run(srv.use_tool(ctx, name="call_model", arguments={"prompt": "hi", "model": "m"}))
    # the same denial a direct tools/call gets: the tool never runs
    assert out.startswith("FAILED: call_model") and "requires scope" in out


def test_use_tool_refuses_unknown_and_recursive_names():
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": None}))
    assert run(srv.use_tool(ctx, name="drop_tables")).startswith("REFUSED: no tool named")
    assert run(srv.use_tool(ctx, name="use_tool")).startswith("REFUSED:")


def test_bad_arguments_come_back_as_text(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": None}))
    assert run(srv.use_tool(ctx, name="call_model", arguments={"model": "m"})).startswith("FAILED: call_model")


def test_the_instructions_tell_the_agent_how_to_find_the_rest():
    assert "discover_tools(need)" in srv._V1_INSTRUCTIONS and "use_tool(name, arguments)" in srv._V1_INSTRUCTIONS


def test_discovery_tools_are_classified():
    assert srv._TOOL_SCOPES["discover_tools"] == srv._READ and srv._TOOL_SCOPES["use_tool"] == srv._READ
    assert srv._V1_ANNOTATIONS["discover_tools"]["read_only_hint"] is True
    assert srv._V1_ANNOTATIONS["use_tool"]["read_only_hint"] is False
