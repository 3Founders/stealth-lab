"""
Offline proving tests for trace_worker.py's inline payload size cap.

CONTEXT: trace_events.tool_input/tool_output (12_trace_ingestion_pipeline.sql)
stored every tool call's payload fully inline as JSONB with no size limit.
The schema's raw_payload_ref TEXT column existed for exactly this ("large
tool outputs get a pointer, not inlined -- same idiom as episodes.content_ref")
but had zero references anywhere in Python. Chaitanya's dogfooding pilot is
producing real hook traces right now, so this closes the gap before volume
makes it matter.

Fully offline: no database, no clock, no network. A FakePool/FakeConn
captures the real SQL + params the same way test_episode_segmentation.py's
writer tests do; raw_payload_dir is always a pytest tmp_path, mirroring the
"fake/temp directory, same convention as fake pools" instruction -- never
the real backend/data/raw_payloads/.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import app.services.trace_worker as tw


# ---------------------------------------------------------------- helpers

def _big_value(n_bytes: int) -> dict:
    """A JSON value whose json.dumps() serialization is at least n_bytes."""
    return {"content": "x" * n_bytes}


# ------------------------------------------- _prepare_payload_columns unit


def test_under_cap_round_trips_inline_exactly_as_before(tmp_path):
    """Regression: a small payload is untouched by this change -- same
    json.dumps() round-trip, no file written, no ref."""
    small = {"file_path": "a.py", "content": "print(1)"}
    tool_input_col, tool_output_col, ref = tw._prepare_payload_columns(
        {"tool_input": small, "tool_output": None}, "dedup-small-1",
        raw_payload_dir=tmp_path,
    )
    assert tool_input_col == json.dumps(small)
    assert tool_output_col is None
    assert ref is None
    assert list(tmp_path.iterdir()) == [], "under-cap payload must never touch disk"


def test_over_cap_caps_inline_and_writes_real_pointer_file(tmp_path):
    """A payload over the cap gets a small overflow marker inline, and a
    real pointer file on disk holding the FULL original content."""
    big = _big_value(40_000)  # > default 32KB cap
    tool_input_col, tool_output_col, ref = tw._prepare_payload_columns(
        {"tool_input": None, "tool_output": big}, "dedup-big-1",
        max_inline_bytes=32 * 1024, raw_payload_dir=tmp_path,
    )
    assert tool_input_col is None
    marker = json.loads(tool_output_col)
    assert marker["_overflow"] is True
    assert marker["size_bytes"] == len(json.dumps(big).encode("utf-8"))
    assert marker["size_bytes"] > 32 * 1024

    assert ref is not None
    ref_path = Path(ref)
    assert ref_path.exists(), "raw_payload_ref must point at a real file"
    assert ref_path.parent == tmp_path

    on_disk = json.loads(ref_path.read_text(encoding="utf-8"))
    assert on_disk == {"tool_output": big}, "the pointer file must hold the FULL content"


def test_custom_threshold_is_honored(tmp_path):
    """The cap is a call-time-consulted param, not a hardcoded literal --
    proven the same way trivial_merge_max_events/oversize_subdivide_events
    are proven retunable elsewhere in this file."""
    value = _big_value(100)

    _, _, ref_default = tw._prepare_payload_columns(
        {"tool_output": value}, "dedup-thresh-1", raw_payload_dir=tmp_path,
    )
    assert ref_default is None, "100 bytes must be inline at the real 32KB default"

    _, col_tiny, ref_tiny = tw._prepare_payload_columns(
        {"tool_output": value}, "dedup-thresh-2",
        max_inline_bytes=10, raw_payload_dir=tmp_path,
    )
    assert ref_tiny is not None, "the same 100-byte value must overflow a 10-byte cap"
    assert json.loads(col_tiny)["_overflow"] is True


def test_both_fields_overflowing_share_one_pointer_file(tmp_path):
    """Named judgment call: the schema has ONE raw_payload_ref column per
    row, not one per field. When both tool_input and tool_output overflow
    on the same row, both land in one pointer file keyed by field name."""
    big_in = _big_value(35_000)
    big_out = _big_value(50_000)
    tool_input_col, tool_output_col, ref = tw._prepare_payload_columns(
        {"tool_input": big_in, "tool_output": big_out}, "dedup-both-1",
        max_inline_bytes=32 * 1024, raw_payload_dir=tmp_path,
    )
    assert json.loads(tool_input_col)["_overflow"] is True
    assert json.loads(tool_output_col)["_overflow"] is True
    on_disk = json.loads(Path(ref).read_text(encoding="utf-8"))
    assert on_disk == {"tool_input": big_in, "tool_output": big_out}


def test_read_overflow_payload_reconstructs_full_content(tmp_path):
    """The read-back helper round-trips exactly what was written."""
    big = _big_value(40_000)
    _, _, ref = tw._prepare_payload_columns(
        {"tool_output": big}, "dedup-readback-1",
        max_inline_bytes=32 * 1024, raw_payload_dir=tmp_path,
    )
    assert tw.read_overflow_payload(ref) == {"tool_output": big}


# --------------------------------------------- _insert_event / FakePool


class FakeConn:
    """Records every statement; INSERT ... RETURNING id always succeeds
    with a fresh fake id (no real ON CONFLICT semantics needed -- these
    tests never insert the same dedup_key twice)."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.executes: list[tuple[str, tuple]] = []

    def transaction(self):
        @asynccontextmanager
        async def _txn():
            yield
        return _txn()

    async def fetchval(self, sql, *args):
        self.inserts.append((" ".join(sql.split()), args))
        return f"row-{len(self.inserts)}"

    async def execute(self, sql, *args):
        self.executes.append((" ".join(sql.split()), args))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def _cm():
            yield conn
        return _cm()


TRACE_EVENTS_INSERT_FRAGMENT = "INSERT INTO trace_events"


def _record(dedup_key: str, tool_output=None, tool_input=None, sequence=0) -> dict:
    return {
        "dedup_key": dedup_key,
        "session_id": "sess-1",
        "event_type": "PostToolUse",
        "sequence": sequence,
        "event": {
            "timestamp": "2026-08-27T10:00:00Z",
            "tool_name": "Read",
            "tool_input": tool_input,
            "tool_output": tool_output,
        },
    }


def test_insert_event_under_cap_matches_prior_behavior(tmp_path):
    """Regression at the INSERT boundary: a small payload's column value
    and raw_payload_ref are exactly what the pre-cap code produced."""
    small = {"content": "print(1)"}
    record = _record("dedup-insert-small", tool_output=small)
    conn = FakeConn()
    asyncio.run(tw._insert_event(conn, record, raw_payload_dir=tmp_path))

    assert len(conn.inserts) == 1
    sql, args = conn.inserts[0]
    assert TRACE_EVENTS_INSERT_FRAGMENT in sql
    # positional args: ..., tool_input(8), tool_output(9), ..., raw_payload_ref(15)
    assert args[8] is None
    assert args[9] == json.dumps(small)
    assert args[15] is None


def test_insert_event_over_cap_carries_marker_not_full_payload(tmp_path):
    """The row that actually lands in trace_events never carries the full
    oversized payload -- only the marker -- while a real file on disk
    holds the complete content."""
    big = _big_value(40_000)
    record = _record("dedup-insert-big", tool_output=big)
    conn = FakeConn()
    asyncio.run(tw._insert_event(
        conn, record, max_inline_bytes=32 * 1024, raw_payload_dir=tmp_path,
    ))

    sql, args = conn.inserts[0]
    tool_output_arg = args[9]
    raw_payload_ref_arg = args[15]

    assert "x" * 100 not in tool_output_arg, "full content must never reach the INSERT row"
    marker = json.loads(tool_output_arg)
    assert marker["_overflow"] is True

    assert raw_payload_ref_arg is not None
    ref_path = Path(raw_payload_ref_arg)
    assert ref_path.exists()
    assert tw.read_overflow_payload(raw_payload_ref_arg) == {"tool_output": big}


# --------------------------------------------- process_collector_file


def test_process_collector_file_caps_oversized_payload_end_to_end(tmp_path):
    """Full pipeline: a JSONL line with an oversized tool_output produces
    a row insert whose tool_output column is the marker (not the full
    payload) and whose raw_payload_ref names a real pointer file -- and a
    sibling small payload round-trips unchanged."""
    big = _big_value(40_000)
    small = {"content": "ok"}
    events_file = tmp_path / "events.jsonl"
    raw_dir = tmp_path / "raw_payloads"
    lines = [
        _record("dedup-e2e-small", tool_output=small, sequence=0),
        _record("dedup-e2e-big", tool_output=big, sequence=1),
    ]
    events_file.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

    conn = FakeConn()
    result = asyncio.run(tw.process_collector_file(
        FakePool(conn), events_file,
        max_inline_bytes=32 * 1024, raw_payload_dir=raw_dir,
    ))
    assert result["inserted"] == 2

    trace_inserts = [
        (sql, args) for sql, args in conn.inserts if TRACE_EVENTS_INSERT_FRAGMENT in sql
    ]
    assert len(trace_inserts) == 2

    small_args = next(a for _, a in trace_inserts if a[2] == 0)  # sequence
    big_args = next(a for _, a in trace_inserts if a[2] == 1)

    assert small_args[9] == json.dumps(small)
    assert small_args[15] is None

    marker = json.loads(big_args[9])
    assert marker["_overflow"] is True
    assert big_args[15] is not None
    ref_path = Path(big_args[15])
    assert ref_path.exists()
    assert ref_path.is_relative_to(raw_dir)
    assert tw.read_overflow_payload(big_args[15]) == {"tool_output": big}
