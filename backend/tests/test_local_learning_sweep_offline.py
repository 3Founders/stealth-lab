"""
P0-1 offline proof: normal local agent work (a real trace transcript from
the collector) becomes a PRIVATE LocalProcedureStore candidate
automatically, DB-free, and is never written to the global substrate.

Real objects only: a real sqlite-backed LocalProcedureStore in tmp_path,
real .jsonl transcript files shaped like the collector's output, the real
sweep function. No DB, no network, no mocks of the code under test. This
module must not import asyncpg / app.db.session (asserted below).
"""
from __future__ import annotations

import json
import sys

import pytest

from app.local_agent.local_learning_sweep import run_local_learning_sweep
from app.local_agent.local_store import LocalProcedureStore


@pytest.fixture()
def store(tmp_path):
    return LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "lib.db"))


def _write_trace(d, name, *records):
    (d / name).write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _real_session(d, name="sess-1.jsonl", goal="add retry logic to the http client"):
    _write_trace(
        d, name,
        {"type": "user", "message": {"content": goal}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/test_client.py -q"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ruff check ."}}]}},
    )


def test_sweep_captures_a_private_candidate_from_a_real_trace(tmp_path, store):
    traces = tmp_path / "traces"; traces.mkdir()
    _real_session(traces)

    summary = run_local_learning_sweep(store, str(traces), max_sessions=5)

    assert summary["sessions_seen"] == 1
    assert summary["sessions_new"] == 1
    assert summary["captured"] == 1
    assert summary["errors"] == 0

    rows = store.list_local_procedures()
    assert len(rows) == 1
    row = store.get_local_procedure(rows[0]["id"])
    assert row["verification_state"] == "candidate"       # never born verified
    assert row["staleness"] == "fresh"
    refs = row["evidence_refs"]
    assert refs and all(r["privacy"] == "local" for r in refs)
    assert refs[0]["source_type"] == "claude_code_trace"
    assert refs[0]["evidence_status"] == "executed"        # observed tool runs
    # retrievable in the local store immediately
    assert store.search_local_procedures("http client retry")


def test_sweep_is_idempotent_across_ticks(tmp_path, store):
    traces = tmp_path / "traces"; traces.mkdir()
    _real_session(traces)

    first = run_local_learning_sweep(store, str(traces), max_sessions=5)
    second = run_local_learning_sweep(store, str(traces), max_sessions=5)

    assert first["captured"] == 1
    assert second["sessions_new"] == 0          # already processed
    assert second["captured"] == 0
    assert len(store.list_local_procedures()) == 1   # no duplicate row


def test_sweep_is_bounded_by_max_sessions(tmp_path, store):
    traces = tmp_path / "traces"; traces.mkdir()
    for i in range(5):
        _real_session(traces, name=f"s{i}.jsonl", goal=f"task number {i} distinct work")

    summary = run_local_learning_sweep(store, str(traces), max_sessions=2)
    assert summary["sessions_seen"] == 5
    assert summary["sessions_processed"] == 2
    # the rest drain on later ticks
    summary2 = run_local_learning_sweep(store, str(traces), max_sessions=2)
    assert summary2["sessions_processed"] == 2


def test_discussion_only_session_is_marked_not_re_read(tmp_path, store):
    traces = tmp_path / "traces"; traces.mkdir()
    _write_trace(traces, "chat.jsonl",
                 {"type": "user", "message": {"content": "what does this repo do?"}})

    first = run_local_learning_sweep(store, str(traces), max_sessions=5)
    assert first["skipped"] == 1
    assert first["captured"] == 0
    assert store.list_local_procedures() == []
    second = run_local_learning_sweep(store, str(traces), max_sessions=5)
    assert second["sessions_new"] == 0          # marked, not re-read every tick


def test_missing_trace_dir_is_a_clean_noop(tmp_path, store):
    summary = run_local_learning_sweep(store, str(tmp_path / "nope"), max_sessions=5)
    assert summary["sessions_seen"] == 0
    assert summary["errors"] == 0


def test_sweep_makes_no_db_connection(tmp_path, monkeypatch):
    """The runtime guarantee that matters: a sweep never opens a Postgres
    connection, even with DATABASE_URL set in the environment. (The other
    tests in this file already run it with DATABASE_URL unset.)"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-be-used/db")

    import asyncpg

    def _boom(*a, **k):
        raise AssertionError("local learning sweep opened a Postgres connection")

    monkeypatch.setattr(asyncpg, "connect", _boom)
    monkeypatch.setattr(asyncpg, "create_pool", _boom)

    traces = tmp_path / "traces"; traces.mkdir()
    _real_session(traces)
    store = LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "lib.db"))
    summary = run_local_learning_sweep(store, str(traces), max_sessions=5)
    assert summary["captured"] == 1

    # module's own import line carries no DB seam
    import ast
    tree = ast.parse(open(
        __import__("app.local_agent.local_learning_sweep", fromlist=["x"]).__file__,
        encoding="utf-8").read())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m == "asyncpg" or m.startswith("app.db") for m in mods), mods
