"""The recommender's MCP surface: two new tools, no existing tool changed.

recommend_models (read scope) and report_model_run (write scope) are registered on both
the full (v2) and the default (v1) surface."""
from __future__ import annotations

import inspect

import app.mcp_server.server as srv


def test_tools_are_registered_and_classified():
    names = {t.name for t in srv.server._tool_manager.list_tools()}
    assert {"recommend_models", "report_model_run"} <= names
    assert srv._TOOL_SCOPES["recommend_models"] == srv._READ
    assert srv._TOOL_SCOPES["report_model_run"] == srv._WRITE


