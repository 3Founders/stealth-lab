"""project_id must survive collector -> record -> agent_traces.

WHY THIS EXISTS. hook_wrapper.py computed project_id and threw it away
(`_ = project_id`). Because derive_preconditions() short-circuits to []
the moment evidence.project_id is missing
(procedure_extraction/derive.py:75), that one discarded line made it
structurally impossible for ANY trace-derived procedure to carry a
precondition -- and a procedure with no preconditions can be matched but
never disqualified, which disables the non-compensatory applicability
cascade entirely.

Measured on the real corpus before the fix: 0 of 18 agent_traces had a
project_id, 0 claims existed with a 'project:' subject, and every
extracted procedure had preconditions = 0.

The dedup_key property is the load-bearing one: project_id rides at
RECORD level, outside `event`, because compute_dedup_key() hashes the
event payload. If it ever moves inside, every already-collected file
silently re-inserts as new rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.trace_collector import append_event, compute_dedup_key


def _event():
    return {"timestamp": "2026-08-29T10:00:00Z", "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"}}


def _lines(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------- the record carries it

def test_project_id_is_written_to_the_record(tmp_path):
    f = tmp_path / "s1.jsonl"
    append_event(_event(), f, session_id="s1", event_type="PostToolUse",
                 sequence=0, project_id="/repo/thing")
    rec = _lines(f)[0]
    assert rec["project_id"] == "/repo/thing"


def test_absent_project_id_is_omitted_not_null(tmp_path):
    """Old files have no such key; a null would make new files a
    different shape for anything reading them."""
    f = tmp_path / "s1.jsonl"
    append_event(_event(), f, session_id="s1", event_type="PostToolUse", sequence=0)
    assert "project_id" not in _lines(f)[0]


def test_project_id_lives_outside_the_event(tmp_path):
    f = tmp_path / "s1.jsonl"
    append_event(_event(), f, session_id="s1", event_type="PostToolUse",
                 sequence=0, project_id="/repo/thing")
    rec = _lines(f)[0]
    assert "project_id" not in rec["event"], (
        "project_id inside `event` would change every dedup_key")


# ------------------------------------------- THE idempotency guarantee

def test_dedup_key_is_unchanged_by_project_id(tmp_path):
    """The whole reason it rides at record level. If this fails, every
    previously-collected event re-inserts as a new row on the next run."""
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    append_event(_event(), a, session_id="s1", event_type="PostToolUse", sequence=0)
    append_event(_event(), b, session_id="s1", event_type="PostToolUse",
                 sequence=0, project_id="/repo/thing")
    assert _lines(a)[0]["dedup_key"] == _lines(b)[0]["dedup_key"]


def test_dedup_key_helper_never_sees_project_id():
    """Belt and braces: the helper's own signature has no project_id, so
    it cannot accidentally be folded in."""
    import inspect
    assert "project_id" not in inspect.signature(compute_dedup_key).parameters


# ------------------------------------------------ the hook wrapper path

def test_hook_wrapper_no_longer_discards_it():
    """The literal regression: `_ = project_id` was the bug."""
    import pathlib
    import app.services.trace_collector as tc

    # trace_collector.py lives at backend/app/services/, so parents[2] is
    # backend/ -- where scripts/ actually is.
    src = (pathlib.Path(tc.__file__).parents[2] / "scripts" / "hook_wrapper.py"
           ).read_text(encoding="utf-8")
    # Comments are stripped first: the fix's own note QUOTES the old
    # `_ = project_id` line to explain what changed, and a naive substring
    # search matches that explanation rather than real code.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "_ = project_id" not in code, "project_id is being discarded again"
    assert "project_id=project_id" in code, "project_id must reach append_event"


# ------------------------------------------------------ the worker path

@pytest.mark.asyncio
async def test_trace_header_insert_carries_project_id():
    """_ensure_trace_header must bind the value, and must backfill a NULL
    on an existing header rather than DO NOTHING -- rows written before
    this change would otherwise stay project-less forever."""
    captured = {}

    class FakeConn:
        async def execute(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args

    from app.services.trace_worker import _ensure_trace_header
    from datetime import datetime, timezone

    await _ensure_trace_header(
        FakeConn(), "t1", "s1", datetime(2026, 8, 29, tzinfo=timezone.utc),
        project_id="/repo/thing",
    )
    assert "project_id" in captured["sql"]
    assert "/repo/thing" in captured["args"]
    assert "ON CONFLICT (trace_id) DO UPDATE" in captured["sql"], (
        "DO NOTHING would never backfill a pre-existing NULL project_id")
    assert "COALESCE(agent_traces.project_id" in captured["sql"], (
        "backfill must be one-way -- never overwrite an existing value")


@pytest.mark.asyncio
async def test_worker_reads_project_id_off_the_record():
    import inspect
    from app.services import trace_worker

    src = inspect.getsource(trace_worker.process_collector_file)
    assert 'record.get("project_id")' in src


# ------------------------------------------- why it matters, pinned

def test_derive_preconditions_still_refuses_without_project_id():
    """The gate this fix feeds. It must keep returning [] rather than
    fabricating -- the fix is to supply a real project_id, never to
    weaken this check."""
    import inspect
    from app.services.procedure_extraction import derive

    src = inspect.getsource(derive.derive_preconditions)
    assert "if not evidence.project_id or evidence.started_at is None:" in src
    assert "return []" in src
