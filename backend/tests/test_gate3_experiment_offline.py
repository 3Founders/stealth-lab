"""
Gate 3 offline tests: runner instrumentation + experimental no-retrieval
arm (Part 2/3) and the deterministic task graders (Part 5).

No real network, no real LLM, no real MCP transport -- the same seam-
swapping pattern test_local_agent_runner_offline.py established:
_open_client_session and _run_local_node are swapped for fakes and the
runner's own behavior is asserted against them.
"""
from __future__ import annotations

import inspect
import json
import os

import pytest

import app.local_agent.runner as runner_module
from app.execution.graph_executor import NodeResult
from tests.fake_embeddings import install_fake_embedder
from gate3_graders import grade_repo, get_task, write_starter_repo


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


_CANDIDATE = {
    "id": "row-1",
    "procedure_id": "proc-gate3",
    "name": "mcp-lazy-tool-schema-loading",
    "verification_state": "candidate",
}

_PROCEDURE = {
    "procedure_id": "proc-gate3",
    "name": "mcp-lazy-tool-schema-loading",
    "verification_state": "candidate",
    "version": 2,
    "steps": [{"order": 0, "goal": "convert the tool listing"}],
}


def _fake_run_node_with_usage():
    calls = []

    async def fake_run_node(node, **kwargs):
        calls.append(node.goal)
        node_notes = kwargs.get("node_notes")
        note = f"step {node.order} ({node.goal}): stop_reason=finished, tool_calls=2"
        if node_notes is not None:
            node_notes.append(note)
        return NodeResult(
            status="success", notes=note,
            data={"files_edited": ["tool_server.py"], "patch": "diff --git ...",
                  "tool_calls": 2, "prompt_tokens": 100,
                  "completion_tokens": 50, "llm_calls": 1})

    return fake_run_node, calls


def _matched_session():
    return FakeClientSession({
        "search_procedures": json.dumps([dict(_CANDIDATE)]),
        "get_procedure": json.dumps(dict(_PROCEDURE)),
        "report_execution": "ok",
    })


def _empty_session():
    return FakeClientSession({"search_procedures": json.dumps([])})


def _swap_seams(monkeypatch, session, run_node):
    monkeypatch.setattr(runner_module, "_open_client_session",
                        lambda url, token: session)
    monkeypatch.setattr(runner_module, "_run_local_node", run_node)


# ---------------------------------------------------------------------------
# Part 3: experimental_no_retrieval arm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arm_a_bypasses_retrieval_and_fabricates_nothing(monkeypatch, tmp_path):
    """A: with experimental_no_retrieval=True the runner never calls
    search_procedures, executes the EXISTING ad-hoc machinery, and reports
    no retrieval -- no fabricated match, empty retrieval_log."""
    install_fake_embedder(monkeypatch)
    session = _matched_session()  # would match if retrieval ran at all
    fake_run_node, calls = _fake_run_node_with_usage()
    _swap_seams(monkeypatch, session, fake_run_node)
    # A REAL (empty) repo directory so the existing ad-hoc machinery --
    # which requires a local store -- actually runs, as it does in
    # production.
    repo = tmp_path / "repo_a"
    repo.mkdir()

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="convert the tool listing",
          repo_path=str(repo),
          allow_unverified=True, experimental_no_retrieval=True)

    assert all(name != "search_procedures" for name, _ in session.calls)
    assert all(name != "get_procedure" for name, _ in session.calls)
    assert all(name != "report_execution" for name, _ in session.calls)
    assert result.matched_procedure is None
    assert result.retrieval_log == []
    assert result.metrics["retrieval_attempted"] is False
    assert result.graph_outcome == "success"  # ad-hoc execution really ran
    assert len(calls) == 1  # single ad-hoc step
    assert any("NO-RETRIEVAL" in note for note in result.node_notes)


@pytest.mark.asyncio
async def test_arm_b_uses_retrieval_and_surfaces_metadata(monkeypatch):
    """B: normal retrieval runs, the matched candidate is executed, and the
    full ranked retrieval evidence + metrics are surfaced."""
    session = _matched_session()
    fake_run_node, _ = _fake_run_node_with_usage()
    _swap_seams(monkeypatch, session, fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="convert the tool listing",
          repo_path="Z:/no/such/dir", allow_unverified=True)

    assert ("search_procedures", {"task": "convert the tool listing",
                                  "require_verified": False, "limit": 3,
                                  "invariant_bindings": "{}"}) in [
        (name, args) for name, args in session.calls]
    assert result.matched_procedure["procedure_id"] == "proc-gate3"
    assert result.metrics["retrieval_attempted"] is True
    assert len(result.retrieval_log) == 1
    entry = result.retrieval_log[0]
    assert entry["rank"] == 0
    assert entry["procedure_id"] == "proc-gate3"
    assert entry["verification_state"] == "candidate"
    assert entry["source"] == "global"
    assert result.metrics["prompt_tokens"] == 100
    assert result.metrics["completion_tokens"] == 50
    assert result.metrics["total_tokens"] == 150
    assert result.metrics["llm_calls"] == 1
    assert result.metrics["steps"] == 1
    assert result.metrics["stop_reason"] == "finished"
    assert result.metrics["cost"] == "not_available"
    assert "report_execution" in [name for name, _ in session.calls]


@pytest.mark.asyncio
async def test_default_behavior_unchanged_when_flag_absent(monkeypatch):
    """Default (flag absent) is the pre-Gate-3 path: retrieval IS attempted
    (a require_verified=True search happens even when nothing matches), and
    the no-match outcome is identical to before."""
    session = _empty_session()
    _swap_seams(monkeypatch, session, _fake_run_node_with_usage()[0])

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="add a new utility function",
          repo_path="Z:/no/such/dir")

    assert result.graph_outcome == "no_match"
    assert result.matched_procedure is None
    assert result.metrics["retrieval_attempted"] is True
    assert ("search_procedures",
            {"task": "add a new utility function",
             "require_verified": True, "limit": 3,
             "invariant_bindings": "{}"}) in [
        (name, args) for name, args in session.calls]
    sig = inspect.signature(runner_module.LocalAgentRunner.run)
    assert sig.parameters["experimental_no_retrieval"].default is False


@pytest.mark.asyncio
async def test_no_retrieval_result_is_fabricated_for_arm_a(monkeypatch):
    """Even when the corpus WOULD match (matched session installed), arm A's
    result carries no procedure evidence whatsoever."""
    session = _matched_session()
    _swap_seams(monkeypatch, session, _fake_run_node_with_usage()[0])

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="convert the tool listing",
          repo_path="Z:/no/such/dir", experimental_no_retrieval=True)

    assert result.matched_procedure is None
    assert result.source is None
    assert result.captured_candidate is None  # nonexistent repo -> no store
    assert result.retrieval_log == []
    assert result.metrics["retrieval_attempted"] is False


@pytest.mark.asyncio
async def test_missing_usage_is_recorded_as_none_not_estimated(monkeypatch):
    """A node whose data lacks token fields yields None metrics -- never a
    zero or an invented number."""

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes="ran",
                          data={"files_edited": [], "patch": ""})

    session = _matched_session()
    _swap_seams(monkeypatch, session, fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="t", repo_path="Z:/no/such/dir",
          allow_unverified=True)

    assert result.metrics["prompt_tokens"] is None
    assert result.metrics["completion_tokens"] is None
    assert result.metrics["total_tokens"] is None
    assert result.metrics["stop_reason"] is None


# ---------------------------------------------------------------------------
# Part 5: deterministic graders
# ---------------------------------------------------------------------------

GOOD_TOOL_SERVER = '''"""Reference correct lazy-schema implementation."""

TOOL_DESCRIPTIONS = {
    "echo": "Echo the given text back unchanged.",
    "add": "Add two numbers together and return the sum.",
    "upper": "Convert the given text to upper case.",
    "count_chars": "Count the characters in the given text.",
}

TOOL_SCHEMAS = {
    "echo": {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
    "add": {"type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"]},
    "upper": {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"]},
    "count_chars": {"type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
}


def list_tools():
    return [{"name": name, "description": TOOL_DESCRIPTIONS[name]}
            for name in TOOL_DESCRIPTIONS]


def get_tool_schema(name):
    if name in TOOL_SCHEMAS:
        return TOOL_SCHEMAS[name]
    return None


def call_tool(name, args):
    if name == "echo":
        return args["text"]
    if name == "add":
        return args["a"] + args["b"]
    if name == "upper":
        return args["text"].upper()
    if name == "count_chars":
        return len(args["text"])
    raise ValueError(f"unknown tool: {name}")
'''


def _write(repo, text):
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, "tool_server.py"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return repo


def test_grader_passes_a_correct_implementation(tmp_path):
    repo = _write(str(tmp_path / "ok"), GOOD_TOOL_SERVER)
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is True, result
    assert result["error"] is None


def test_grader_fails_schema_leakage(tmp_path):
    repo = _write(str(tmp_path / "leak"), GOOD_TOOL_SERVER.replace(
        'return [{"name": name, "description": TOOL_DESCRIPTIONS[name]}',
        'return [{"name": name, "description": TOOL_DESCRIPTIONS[name],\n'
        '             "input_schema": TOOL_SCHEMAS[name]}'))
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is False
    assert result["checks"]["listing_lightweight"] is False


def test_grader_fails_wrong_targeted_schema(tmp_path):
    repo = _write(str(tmp_path / "wrong"), GOOD_TOOL_SERVER.replace(
        '"required": ["text"]},\n    "add"', '"required": []},\n    "add"', 1))
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is False
    assert result["checks"]["targeted_schema_exact"] is False


def test_grader_fails_missing_targeted_retrieval(tmp_path):
    repo = _write(str(tmp_path / "noget"),
                  GOOD_TOOL_SERVER.replace(
                      'def get_tool_schema(name):', 'def _unused(name):'))
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is False


def test_grader_fails_call_tool_regression(tmp_path):
    repo = _write(str(tmp_path / "regr"), GOOD_TOOL_SERVER.replace(
        'return args["a"] + args["b"]', 'return args["a"] - args["b"]'))
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is False
    assert result["checks"]["call_tool_regression"] is False


def test_grader_fails_malformed_implementation(tmp_path):
    repo = _write(str(tmp_path / "bad"), "def broken(:\n")
    result = grade_repo("task1_lazy_multi_tool", repo)
    assert result["grader_success"] is False
    assert result["checks"]["import_ok"] is False


def test_starting_repo_never_passes_its_own_grader(tmp_path):
    """The starter (leaky/broken) repository must fail its task's grader --
    a declared success can never become a success without real work."""
    for task_id in ("task1_lazy_multi_tool", "task2_lazy_new_tool",
                    "na_broken_echo"):
        repo = str(tmp_path / task_id)
        write_starter_repo(task_id, repo)
        result = grade_repo(task_id, repo)
        assert result["grader_success"] is False, (task_id, result)


def test_grader_result_is_independent_of_any_agent_claim():
    """The grader API takes only (task_id, repo_path) -- there is no
    parameter through which a self-report could influence the outcome
    (Part 5: a declared success is never itself evidence)."""
    sig = inspect.signature(grade_repo)
    assert list(sig.parameters) == ["task_id", "repo_path"]


def test_task_definitions_are_naturally_worded():
    """Task text must not leak the procedure name or the phrase
    'lazy loading' (Part 4 discipline)."""
    for task_id in ("task1_lazy_multi_tool", "task2_lazy_new_tool",
                    "na_broken_echo"):
        text = get_task(task_id)["task_text"]
        assert "mcp-lazy-tool-schema-loading" not in text
        assert "lazy" not in text.lower()
        assert "StealthLab" not in text
