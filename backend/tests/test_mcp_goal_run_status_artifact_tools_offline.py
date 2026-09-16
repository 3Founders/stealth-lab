"""
Offline (no real DB, no real network) thin-wrapper tests for the three
new meta-harness/execu.md Sec 14 MCP tools this pass adds:
get_goal_run_status, list_goal_artifacts, get_goal_artifact. All three
are pure local-filesystem reads -- no pool use at all -- so these tests
never touch a database, only a real tmp_path.
"""
from __future__ import annotations

import asyncio
import base64
import json

import app.mcp_server.server as srv
from app.execution.goal_execution import write_goal_run_md_file
from app.stealth.artifacts import write_execution_artifacts
from app.stealth.pipe_format import GoalRunLine, render_goal_run_md


def _run(coro):
    return asyncio.run(coro)


class FakeRequestContext:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


# ---------------------------------------------------------------------
# get_goal_run_status
# ---------------------------------------------------------------------


def test_get_goal_run_status_refuses_when_no_file_exists(tmp_path):
    ctx = FakeContext()
    result = _run(srv.get_goal_run_status(workspace_root=str(tmp_path), ctx=ctx))
    assert result.startswith("REFUSED:")
    assert "workspace_root" in result


def test_get_goal_run_status_returns_the_real_parsed_file(tmp_path):
    nodes = [GoalRunLine(goal_id="G-1", kind="implementation", status="success", implementation_id="I-1")]
    write_goal_run_md_file(str(tmp_path), render_goal_run_md("E-1", "success", nodes))
    ctx = FakeContext()
    raw = _run(srv.get_goal_run_status(workspace_root=str(tmp_path), ctx=ctx))
    result = json.loads(raw)
    assert result["execution_id"] == "E-1"
    assert result["outcome"] == "success"
    assert result["nodes"][0]["goal_id"] == "G-1"
    assert result["nodes"][0]["implementation_id"] == "I-1"


# ---------------------------------------------------------------------
# list_goal_artifacts
# ---------------------------------------------------------------------


def test_list_goal_artifacts_on_empty_workspace_is_an_honest_empty_list(tmp_path):
    ctx = FakeContext()
    raw = _run(srv.list_goal_artifacts(workspace_root=str(tmp_path), ctx=ctx))
    assert json.loads(raw) == []


def test_list_goal_artifacts_finds_real_written_files(tmp_path):
    write_execution_artifacts(str(tmp_path), "G-1", "E-1", {"out.txt": b"hello"})
    ctx = FakeContext()
    raw = _run(srv.list_goal_artifacts(workspace_root=str(tmp_path), ctx=ctx))
    entries = json.loads(raw)
    assert len(entries) == 1
    assert entries[0]["goal_id"] == "G-1"
    assert entries[0]["execution_id"] == "E-1"
    assert entries[0]["filename"] == "out.txt"


# ---------------------------------------------------------------------
# get_goal_artifact
# ---------------------------------------------------------------------


def test_get_goal_artifact_returns_real_bytes_base64_encoded(tmp_path):
    write_execution_artifacts(str(tmp_path), "G-1", "E-1", {"out.txt": b"hello world"})
    ctx = FakeContext()
    raw = _run(srv.get_goal_artifact(
        workspace_root=str(tmp_path), goal_id="G-1", execution_id="E-1", filename="out.txt", ctx=ctx,
    ))
    result = json.loads(raw)
    assert result["filename"] == "out.txt"
    assert result["size_bytes"] == 11
    assert base64.b64decode(result["content_base64"]) == b"hello world"


def test_get_goal_artifact_refuses_when_missing(tmp_path):
    ctx = FakeContext()
    raw = _run(srv.get_goal_artifact(
        workspace_root=str(tmp_path), goal_id="G-1", execution_id="E-1", filename="nope.txt", ctx=ctx,
    ))
    assert raw.startswith("REFUSED:")


def test_get_goal_artifact_refuses_on_path_escape_attempt(tmp_path):
    write_execution_artifacts(str(tmp_path), "G-1", "E-1", {"out.txt": b"x"})
    ctx = FakeContext()
    raw = _run(srv.get_goal_artifact(
        workspace_root=str(tmp_path), goal_id="G-1", execution_id="E-1",
        filename="../../../etc/passwd", ctx=ctx,
    ))
    assert raw.startswith("REFUSED:")
