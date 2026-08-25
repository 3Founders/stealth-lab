import io
import json
import sys

import stealthlab_connect as slc
from stealthlab_connect import collector_entry


def _run_cli(monkeypatch, payload_text, argv=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload_text))
    return collector_entry.main(list(argv or []))


def _post_tool_use_payload(token: str) -> str:
    return json.dumps({
        "session_id": "hook-sess-1",
        "hook_event_name": "PostToolUse",
        "cwd": "/proj",
        "tool_name": "Bash",
        "tool_input": {"command": f"export TOKEN={token}"},
        "tool_output": "done",
    })


def test_hook_writes_redacted_record_to_project_trace_dir(monkeypatch, tmp_path):
    secret = "ghp_" + "b" * 36
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("STEALTHLAB_TRACE_DIR", raising=False)
    code = _run_cli(monkeypatch, _post_tool_use_payload(secret))
    assert code == 0
    trace_file = tmp_path / ".claude" / "traces" / "hook-sess-1.jsonl"
    raw = trace_file.read_text(encoding="utf-8")
    assert secret not in raw
    record = json.loads(raw.splitlines()[0])
    assert record["event_type"] == "PostToolUse"
    assert record["event"]["success"] is True
    assert "[REDACTED:github_token]" in record["event"]["tool_input"]["command"]


def test_hook_trace_dir_env_override_and_file_flag(monkeypatch, tmp_path):
    target = tmp_path / "custom.jsonl"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code = _run_cli(monkeypatch, _post_tool_use_payload("x" * 10),
                    ["--file", str(target)])
    assert code == 0
    assert target.is_file()
    assert json.loads(target.read_text().splitlines()[0])["session_id"] == "hook-sess-1"


def test_malformed_json_still_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    code = _run_cli(monkeypatch, "{not json")
    assert code == 0
    assert not (tmp_path / ".claude" / "traces").exists()


def test_missing_session_id_drops_event_but_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    code = _run_cli(monkeypatch, json.dumps({"hook_event_name": "Stop"}))
    assert code == 0
    assert not (tmp_path / ".claude" / "traces").exists()


def test_empty_stdin_is_a_clean_noop(monkeypatch):
    code = _run_cli(monkeypatch, "")
    assert code == 0


def test_non_tool_event_keeps_documented_fields_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = json.dumps({
        "session_id": "hook-sess-2",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "ship it",
        "tool_input": {"should": "not appear"},
    })
    _run_cli(monkeypatch, payload)
    record = json.loads(
        (tmp_path / ".claude" / "traces" / "hook-sess-2.jsonl").read_text().splitlines()[0]
    )
    event = record["event"]
    assert event["prompt"] == "ship it"
    assert "tool_input" not in event


def test_build_event_matches_backend_reference_mapping():
    payload = {
        "session_id": "s",
        "hook_event_name": "PostToolUseFailure",
        "error": "boom",
        "tool_response": {"out": 1},
    }
    event = slc.build_event(payload)
    assert event["success"] is False
    assert event["error"] == "boom"
    assert event["tool_response"] == {"out": 1}
    assert "timestamp" in event and "actor_id" in event


def test_tool_response_normalized_to_tool_output_in_stored_record(tmp_path):
    import json as jsonlib

    record = slc.collect_stdin_payload(
        {"session_id": "norm-1", "hook_event_name": "PostToolUse",
         "tool_name": "Read", "tool_input": {"file_path": "a.py"},
         "tool_response": {"out": 1}},
        file_path=tmp_path / "t.jsonl",
    )
    assert record["event"]["tool_output"] == {"out": 1}
    assert "tool_response" not in record["event"]
    assert jsonlib.loads((tmp_path / "t.jsonl").read_text()) == record
