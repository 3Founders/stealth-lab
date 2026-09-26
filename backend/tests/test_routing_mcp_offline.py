"""The recommender's MCP surface: two new tools, nothing existing changed.

recommend_models (read scope) and report_model_run (write scope) are registered on the
full (v2) surface. The v1 surface stays exactly as specified (three tools) -- exposing
the recommender there is a separate product decision (add both names to V1_TOOLS)."""
from __future__ import annotations

import inspect

import app.mcp_server.server as srv


def test_tools_are_registered_and_classified():
    names = {t.name for t in srv.server._tool_manager.list_tools()}
    assert {"recommend_models", "report_model_run"} <= names
    assert srv._TOOL_SCOPES["recommend_models"] == srv._READ
    assert srv._TOOL_SCOPES["report_model_run"] == srv._WRITE


def test_v1_surface_and_report_execution_are_unchanged():
    assert srv.V1_TOOLS == frozenset({"find_ways", "report_discovery", "submit_way"})
    params = list(inspect.signature(srv.report_execution).parameters)
    assert params == ["procedure_id", "success", "context_key", "ctx", "steps_used", "success_criteria",
                      "failure_class", "observations_json", "tool_sequence_json", "task_description", "session_id"]
