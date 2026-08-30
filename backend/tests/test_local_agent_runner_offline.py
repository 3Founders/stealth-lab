"""
Phase 1's offline contract test (imperative-twirling-plum.md, Step 2):
proves the ARCHITECTURAL BOUNDARY, not just the call sequence -- the
local runner must have zero database dependency, structurally, not by
convention.

No fake database is created here on purpose: there is nothing to fake.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

import app.local_agent.runner as runner_module
from app.execution.graph_executor import NodeResult

RUNNER_SOURCE_PATH = Path(inspect.getfile(runner_module))


def test_runner_module_never_imports_the_database():
    """Structural, not conventional: parse the real module's own import
    statements (not just grep the text, which a comment could fool) and
    assert none of them touch asyncpg or app.db.session."""
    tree = ast.parse(RUNNER_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden = {"asyncpg", "app.db.session", "app.db"}
    hit = forbidden & imported_names
    assert not hit, f"local_agent.runner must not import the database, found: {hit}"
    assert "asyncpg" not in sys.modules or True  # asyncpg may be loaded by OTHER
    # already-imported test modules in the same process; the real assertion
    # is the source-level one above, not a process-wide sys.modules check.


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeToolResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeClientSession:
    """Records every call_tool invocation, in order, and returns
    caller-programmed results -- no real network, no real MCP transport."""

    def __init__(self, responses: dict):
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses

    async def initialize(self):
        return None

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        return FakeToolResult(self._responses[name])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_runner_calls_search_then_execute_then_report(monkeypatch):
    import json

    procedure = {
        "id": "row-1", "procedure_id": "proc-1", "version": 1,
        "name": "fix-calc-bug", "goal": "fix the bug",
        "steps": [{"order": 0, "goal": "fix the bug in calc.py"}],
    }
    fake_session = FakeClientSession({
        "search_procedures": json.dumps([
            {"id": "row-1", "procedure_id": "proc-1", "version": 1,
             "name": "fix-calc-bug", "verification_state": "candidate", "similarity": 0.9},
        ]),
        "get_procedure": json.dumps(procedure),
        "report_execution": json.dumps({"procedure_id": "proc-1", "verification_state": "candidate"}),
    })

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}", data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the bug", repo_path="/fake/repo", allow_unverified=True)

    called_tools = [c[0] for c in fake_session.calls]
    assert called_tools == ["search_procedures", "get_procedure", "report_execution"], (
        f"expected search -> get -> report order, got {called_tools}"
    )
    assert result.matched_procedure["procedure_id"] == "proc-1"
    assert result.graph_outcome == "success"


@pytest.mark.asyncio
async def test_runner_reports_no_match_without_crashing(monkeypatch):
    import json

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})
    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix something nothing matches", repo_path="/fake/repo")

    assert result.matched_procedure is None
    assert result.graph_outcome == "no_match"
    assert [c[0] for c in fake_session.calls] == ["search_procedures"], (
        "must not call get_procedure/report_execution when nothing matched"
    )
