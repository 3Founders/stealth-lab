import json

import pytest

import stealthlab_connect as slc


@pytest.fixture()
def trace_file(tmp_path):
    return tmp_path / "traces" / "sess-1.jsonl"


def test_append_round_trip_record_shape(trace_file):
    record = slc.append_trace_event(
        {"hook_event_name": "UserPromptSubmit", "prompt": "hello"},
        trace_file,
        session_id="sess-1",
        event_type="UserPromptSubmit",
    )
    assert set(record) == {"dedup_key", "session_id", "event_type", "sequence", "event"}
    assert record["sequence"] == 0
    lines = trace_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record


def test_redacts_known_token_before_write(trace_file):
    secret = "ghp_" + "a" * 36
    slc.append_trace_event(
        {"tool_name": "Bash", "tool_input": {"command": f"export TOKEN={secret}"},
         "tool_output": "ok"},
        trace_file,
        session_id="sess-1",
        event_type="PostToolUse",
    )
    raw = trace_file.read_text(encoding="utf-8")
    assert secret not in raw
    assert "[REDACTED:github_token]" in raw
    record = json.loads(raw.splitlines()[0])
    assert record["event"]["_redaction"]["patterns_matched"] == ["github_token"]


def test_sensitive_path_excludes_tool_output_wholesale(trace_file):
    slc.append_trace_event(
        {"tool_name": "Read",
         "tool_input": {"file_path": "C:\\Users\\dev\\proj\\.env"},
         "tool_output": {"content": "DATABASE_URL=postgres://real-secret"}},
        trace_file,
        session_id="sess-1",
        event_type="PostToolUse",
    )
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[0])
    assert "real-secret" not in json.dumps(record)
    assert record["event"]["tool_input"] == "[EXCLUDED: sensitive path]"
    assert record["event"]["tool_output"] == "[EXCLUDED: sensitive path]"
    assert "sensitive_path" in record["event"]["_redaction"]["patterns_matched"]


def test_auto_sequence_increments_under_concurrent_style_repeats(trace_file):
    first = slc.append_trace_event({"n": 1}, trace_file, session_id="s", event_type="Stop")
    second = slc.append_trace_event({"n": 2}, trace_file, session_id="s", event_type="Stop")
    assert (first["sequence"], second["sequence"]) == (0, 1)


def test_explicit_sequence_and_dedup_determinism(trace_file):
    collector = slc.load_trace_collector_module()
    key_a = collector.compute_dedup_key("s", "t", 7, {"payload": 1})
    key_b = collector.compute_dedup_key("s", "t", 7, {"payload": 1})
    key_c = collector.compute_dedup_key("s", "t", 7, {"payload": 2})
    assert key_a == key_b
    assert key_a != key_c
    rec1 = slc.append_trace_event({"payload": 1}, trace_file, session_id="s",
                                  event_type="t", sequence=7)
    assert rec1["dedup_key"] == key_a


def test_drop_count_visible_after_worker_seen_compaction(tmp_path):
    trace_file = tmp_path / "compact.jsonl"
    for i in range(5):
        slc.append_trace_event({"i": i}, trace_file, session_id="s", event_type="Stop",
                               max_lines=5)
    assert trace_file.read_text().count("\n") == 5
    collector = slc.load_trace_collector_module()
    collector.mark_worker_seen(trace_file, 5)
    slc.append_trace_event({"i": 5}, trace_file, session_id="s", event_type="Stop",
                           max_lines=5)
    assert slc.trace_drop_count(trace_file) == 1
    lines = trace_file.read_text().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[0])["event"] == {"i": 1}
