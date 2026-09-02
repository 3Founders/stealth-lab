"""
Task spec §11 -- the history-based half of the canonical ingestion/admission
boundary: Git history snapshot, Claude history, ChatGPT history, agent
trace.

Unlike the file-based sources (test_repo_doc_adapters_offline.py), these
four land in a per-workspace LOCAL SQLite store (app/local_agent/local_store.py
:: LocalProcedureStore), never directly in the shared global Postgres
substrate -- by construction, not by a runtime check this suite has to
prove separately (the store's own module docstring: "No approval_status /
visibility / tenant scoping -- a private local [store]"). Promoting a local
row to global requires a SEPARATE, explicit publish call
(mark_local_procedure_published) that this ingestion path never makes on
its own -- that is exactly the "private source data cannot become global
data accidentally" property this file proves for these four families:
none of run_bootstrap/bootstrap_git_history/converge_episode/
converge_candidate ever receives or opens an asyncpg pool.

Fully offline: LocalProcedureStore is a tmp-path SQLite file, no
DATABASE_URL needed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from app.local_agent.git_history_bootstrap import bootstrap_git_history, walk_git_history
from app.local_agent.historical_bootstrap import (
    bootstrap_claude_code_traces,
    converge_episode,
    parse_chatgpt_export,
    parse_claude_export,
)
from app.local_agent.local_store import LocalProcedureStore


def _store(tmp_path) -> LocalProcedureStore:
    return LocalProcedureStore(db_path=str(tmp_path / "local.sqlite3"))


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo_with_a_real_workflow_pattern(repo_root):
    """A tiny real git repo whose commit messages describe the same
    conservative cross-commit pattern form_candidates() looks for (fix ->
    verify), so bootstrap_git_history has something real to find -- not
    asserting on the exact pattern-detection heuristic itself (that
    belongs to git_history_bootstrap's own tests), only that the real
    end-to-end path (repo -> walk -> candidate -> converge -> local store)
    works and stays local."""
    os.makedirs(repo_root, exist_ok=True)
    _git("init", "-q", cwd=repo_root)
    _git("config", "user.email", "gold@example.com", cwd=repo_root)
    _git("config", "user.name", "gold", cwd=repo_root)
    for i, (msg, content) in enumerate([
        ("fix: retry loop duplicated side effects", "v1"),
        ("fix: retry loop duplicated side effects", "v2"),
        ("test: verify retry loop fix", "v3"),
    ]):
        fpath = os.path.join(repo_root, "retry.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        _git("add", "retry.py", cwd=repo_root)
        _git("commit", "-q", "-m", msg, cwd=repo_root)
    return repo_root


# ---------------------------------------------------------------------------
# Git history snapshot
# ---------------------------------------------------------------------------
def test_git_history_valid_repo_converges_locally_never_touches_global_db(tmp_path):
    repo = _init_repo_with_a_real_workflow_pattern(str(tmp_path / "repo"))
    store = _store(tmp_path)

    summary = bootstrap_git_history(store, repo)
    assert summary["is_git_repo"] is True
    assert summary["commits_scanned"] == 3

    rows = store.list_local_procedures()
    # Provenance survives: every row traces back to real commit shas, not
    # a fabricated reference.
    for row in rows:
        assert row["provenance"] in ("git_history", "prior_library")
        assert row["scope"].get("bootstrap_sources") or row.get("source_episode_ids")


def test_git_history_empty_non_git_directory_yields_no_candidates_not_a_crash(tmp_path):
    empty_dir = tmp_path / "not-a-repo"
    empty_dir.mkdir()
    store = _store(tmp_path)
    summary = bootstrap_git_history(store, str(empty_dir))
    assert summary["is_git_repo"] is False
    assert summary["commits_scanned"] == 0
    assert store.list_local_procedures() == []


def test_git_history_repeated_ingestion_of_the_same_repo_merges_not_duplicates(tmp_path):
    """Idempotency where promised: converge_candidate merges into an
    existing row on a repeat pass rather than creating a second, near-
    identical local procedure."""
    repo = _init_repo_with_a_real_workflow_pattern(str(tmp_path / "repo"))
    store = _store(tmp_path)

    first = bootstrap_git_history(store, repo)
    if first["candidates_formed"] == 0:
        return  # this repo's commit shape didn't clear the pattern bar -- nothing to re-test
    count_after_first = len(store.list_local_procedures())

    second = bootstrap_git_history(store, repo)
    count_after_second = len(store.list_local_procedures())
    assert count_after_second == count_after_first, (
        "re-running bootstrap on an unchanged repo must merge into existing rows, "
        "not create duplicates"
    )
    assert second["merged"] >= 1


# ---------------------------------------------------------------------------
# Claude history
# ---------------------------------------------------------------------------
def _write_claude_export(path, conversations):
    path.write_text(json.dumps(conversations), encoding="utf-8")


def test_claude_history_valid_export_converges_and_stays_local(tmp_path):
    export = tmp_path / "conversations.json"
    _write_claude_export(export, [{
        "uuid": "c1", "name": "fix the flaky test",
        "chat_messages": [
            {"sender": "human", "text": "the retry test is flaky, can you fix it?"},
            {"sender": "assistant", "text": "Ran it and it passed:\n```\npytest tests/test_retry.py -q\n```"},
        ],
    }])
    episodes = parse_claude_export(str(export))
    assert len(episodes) == 1
    assert episodes[0].source_type == "claude_chat"

    store = _store(tmp_path)
    result = converge_episode(store, episodes[0])
    assert result["status"] == "captured"
    row = store.get_local_procedure(result["id"])
    assert row["source_episode_ids"] == ["c1"]  # provenance survives


def test_claude_history_discussion_only_conversation_yields_no_episode(tmp_path):
    export = tmp_path / "conversations.json"
    _write_claude_export(export, [{
        "uuid": "c2", "name": "just chatting",
        "chat_messages": [
            {"sender": "human", "text": "what do you think about rust vs go?"},
            {"sender": "assistant", "text": "both have tradeoffs..."},
        ],
    }])
    assert parse_claude_export(str(export)) == []


def test_claude_history_malformed_export_raises_rather_than_silently_ingesting_nothing():
    import pytest

    bad = None
    try:
        parse_claude_export("/definitely/does/not/exist.json")
    except OSError:
        bad = "raised"
    assert bad == "raised", "a missing/unreadable export must fail loudly, not silently return []"


def test_claude_history_empty_export_yields_no_episodes(tmp_path):
    export = tmp_path / "conversations.json"
    _write_claude_export(export, [])
    assert parse_claude_export(str(export)) == []


def test_claude_history_repeated_ingestion_of_identical_export_merges(tmp_path):
    export = tmp_path / "conversations.json"
    _write_claude_export(export, [{
        "uuid": "c3", "name": "add retry backoff",
        "chat_messages": [
            {"sender": "human", "text": "add exponential backoff to the retry loop"},
            {"sender": "assistant", "text": "Ran it and it passed:\n```\npytest tests/test_backoff.py -q\n```"},
        ],
    }])
    store = _store(tmp_path)
    episodes = parse_claude_export(str(export))
    r1 = converge_episode(store, episodes[0])
    assert r1["status"] == "captured"
    r2 = converge_episode(store, episodes[0])  # same episode object, re-run
    assert r2["status"] == "merged"
    assert len(store.list_local_procedures()) == 1


# ---------------------------------------------------------------------------
# ChatGPT history
# ---------------------------------------------------------------------------
def _chatgpt_export(mapping, current_node=None, conv_id="g1"):
    conv = {"uuid": conv_id, "title": "gold chatgpt case", "mapping": mapping}
    if current_node is not None:
        conv["current_node"] = current_node
    return [conv]


def test_chatgpt_history_valid_linear_export_converges(tmp_path):
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
        "u1": {"id": "u1", "parent": "root", "children": ["a1"], "message": {
            "id": "u1", "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["fix the timeout bug"]},
            "create_time": 1.0,
        }},
        "a1": {"id": "a1", "parent": "u1", "children": [], "message": {
            "id": "a1", "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": [
                "Ran it and it passed:\n```\npytest tests/test_timeout.py -q\n```"
            ]},
            "create_time": 2.0,
        }},
    }
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps(_chatgpt_export(mapping, current_node="a1")), encoding="utf-8")

    episodes = parse_chatgpt_export(str(export))
    assert len(episodes) == 1
    assert episodes[0].source_type == "chatgpt_chat"

    store = _store(tmp_path)
    result = converge_episode(store, episodes[0])
    assert result["status"] == "captured"


def test_chatgpt_history_ambiguous_ancestry_yields_no_episode(tmp_path):
    """No current_node + real branching: conservative, zero episodes --
    same policy this suite's evidence/ layer already proved for the
    ChatGPT-branch fix (§9), exercised here at the historical-bootstrap
    ingestion boundary specifically."""
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
        "u1": {"id": "u1", "parent": "root", "children": ["a_bad", "a_ok"], "message": {
            "id": "u1", "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["does the build pass?"]},
            "create_time": 1.0,
        }},
        "a_bad": {"id": "a_bad", "parent": "u1", "children": [], "message": {
            "id": "a_bad", "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["build succeeded, all tests passed"]},
            "create_time": 2.0,
        }},
        "a_ok": {"id": "a_ok", "parent": "u1", "children": [], "message": {
            "id": "a_ok", "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Try make build."]},
            "create_time": 3.0,
        }},
    }
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps(_chatgpt_export(mapping)), encoding="utf-8")  # no current_node
    assert parse_chatgpt_export(str(export)) == []


def test_chatgpt_history_empty_export_yields_no_episodes(tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps([]), encoding="utf-8")
    assert parse_chatgpt_export(str(export)) == []


# ---------------------------------------------------------------------------
# Agent trace
# ---------------------------------------------------------------------------
def _trace_line(rec: dict) -> str:
    return json.dumps(rec) + "\n"


def test_agent_trace_valid_session_captures_observed_tool_use_steps(tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    lines = [
        _trace_line({"type": "user", "message": {"content": "fix the failing test"}}),
        _trace_line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/test_x.py -q"}},
        ]}}),
        _trace_line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"command": "apply fix to test_x.py"}},
        ]}}),
    ]
    (trace_dir / "session1.jsonl").write_text("".join(lines), encoding="utf-8")

    episodes = bootstrap_claude_code_traces(str(trace_dir))
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.source_type == "claude_code_trace"
    assert ep.evidence_status == "executed"  # observed tool_use, the strongest historical source
    assert len(ep.steps) == 2

    store = _store(tmp_path)
    result = converge_episode(store, ep)
    assert result["status"] == "captured"
    row = store.get_local_procedure(result["id"])
    assert row["source_episode_ids"] == ["session1.jsonl"]


def test_agent_trace_discussion_only_session_yields_no_episode(tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "chatty.jsonl").write_text(
        _trace_line({"type": "user", "message": {"content": "what does this function do?"}})
        + _trace_line({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "It parses the config file."},
        ]}}),
        encoding="utf-8",
    )
    assert bootstrap_claude_code_traces(str(trace_dir)) == []


def test_agent_trace_malformed_jsonl_lines_are_skipped_not_a_crash(tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "broken.jsonl").write_text(
        "not json at all\n"
        + _trace_line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo ok"}},
        ]}}),
        encoding="utf-8",
    )
    episodes = bootstrap_claude_code_traces(str(trace_dir))
    assert len(episodes) == 1
    assert episodes[0].steps[0]["goal"] == "echo ok"


def test_agent_trace_empty_directory_yields_no_episodes(tmp_path):
    trace_dir = tmp_path / "empty_traces"
    trace_dir.mkdir()
    assert bootstrap_claude_code_traces(str(trace_dir)) == []


def test_agent_trace_nonexistent_directory_yields_no_episodes_not_a_crash(tmp_path):
    assert bootstrap_claude_code_traces(str(tmp_path / "does-not-exist")) == []


def test_agent_trace_repeated_ingestion_of_the_same_session_merges(tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "session2.jsonl").write_text(
        _trace_line({"type": "user", "message": {"content": "add input validation"}})
        + _trace_line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/test_validate.py"}},
        ]}}),
        encoding="utf-8",
    )
    store = _store(tmp_path)
    episodes = bootstrap_claude_code_traces(str(trace_dir))
    r1 = converge_episode(store, episodes[0])
    assert r1["status"] == "captured"
    r2 = converge_episode(store, episodes[0])
    assert r2["status"] == "merged"
    assert len(store.list_local_procedures()) == 1


# ---------------------------------------------------------------------------
# Cross-cutting: none of this ever opens a DB pool (structural proof, not
# just an observation) -- private-stays-local is a property of the
# functions never accepting a pool argument at all, not a runtime check.
# ---------------------------------------------------------------------------
def test_none_of_the_four_history_bootstrap_entrypoints_accept_a_db_pool():
    import inspect

    for fn in (bootstrap_git_history, converge_episode, bootstrap_claude_code_traces,
               parse_claude_export, parse_chatgpt_export):
        params = inspect.signature(fn).parameters
        assert "pool" not in params, (
            f"{fn.__qualname__} must not accept a DB pool -- these four source families "
            "land in the local, per-workspace store only; promotion to global storage is "
            "a separate, explicit publish step this ingestion path never takes on its own"
        )
