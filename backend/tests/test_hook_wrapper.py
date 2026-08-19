"""
Real tests for scripts/hook_wrapper.py. Cannot test against a real
Claude Code process (none available in this sandbox, same honest limit
as trace_collector.py's own docstring states) -- these tests exercise
build_event() directly against synthetic payloads shaped exactly like
the documented common/event-specific field lists in
.scratch/memory-substrate/research/claude-code-hook-schema.md, plus
real subprocess invocations of the script itself to confirm the
fail-safe (always exit 0) contract holds even under malformed input.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from hook_wrapper import build_event  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "hook_wrapper.py"


def test_pre_tool_use_maps_tool_fields():
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PreToolUse",
        "cwd": "/home/user/my-project",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
    }
    event = build_event(payload)
    assert event["hook_event_name"] == "PreToolUse"
    assert event["tool_name"] == "Bash"
    assert event["tool_input"] == {"command": "ls -la"}
    assert "timestamp" in event


def test_post_tool_use_infers_success_true():
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": "x.py"},
        "tool_output": {"result": "ok"},
    }
    event = build_event(payload)
    assert event["success"] is True
    assert event["tool_output"] == {"result": "ok"}


def test_post_tool_use_failure_infers_success_false():
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "error": {"message": "command not found"},
    }
    event = build_event(payload)
    assert event["success"] is False
    assert event["error"] == {"message": "command not found"}


def test_tool_response_field_is_passed_through_for_redaction_layer_to_normalize():
    """build_event() deliberately does NOT pick between tool_output and
    tool_response -- that normalization already happens in
    trace_redaction.redact_event() (A6 fix). Passing both through
    undecided here avoids duplicating that logic in two places."""
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_response": {"stdout": "hello"},
    }
    event = build_event(payload)
    assert event["tool_response"] == {"stdout": "hello"}
    assert "tool_output" not in event


def test_subagent_actor_id_comes_from_agent_id():
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PreToolUse",
        "agent_id": "subagent-42",
        "tool_name": "Read",
    }
    event = build_event(payload)
    assert event["actor_id"] == "subagent-42"


def test_main_agent_has_no_actor_id():
    payload = {"session_id": "abc123", "hook_event_name": "PreToolUse", "tool_name": "Read"}
    event = build_event(payload)
    assert event["actor_id"] is None


def test_non_tool_shaped_event_does_not_assume_tool_fields():
    payload = {
        "session_id": "abc123",
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    event = build_event(payload)
    assert event["hook_event_name"] == "SessionStart"
    assert event["source"] == "startup"
    assert "tool_name" not in event
    assert "tool_input" not in event


def test_missing_tool_call_id_is_none_not_a_crash():
    """Real open question flagged in build_event()'s docstring: no
    documented field name for tool_call_id. Must degrade to None, not
    KeyError."""
    payload = {"session_id": "abc123", "hook_event_name": "PreToolUse", "tool_name": "Bash"}
    event = build_event(payload)
    assert event["tool_call_id"] is None


def test_script_always_exits_0_on_valid_payload(tmp_path: Path):
    """Real subprocess invocation -- exit code 2 BLOCKS the user's tool
    call per the documented hook contract, so this must never happen
    regardless of payload shape."""
    env = {"STEALTHLAB_TRACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    payload = {
        "session_id": "test-sess-1", "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_input": {"command": "echo hi"},
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0
    trace_file = tmp_path / "test-sess-1.jsonl"
    assert trace_file.exists()
    record = json.loads(trace_file.read_text().splitlines()[0])
    assert record["session_id"] == "test-sess-1"


def test_script_exits_0_on_malformed_json_stdin(tmp_path: Path):
    env = {"STEALTHLAB_TRACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input="{not valid json", capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, "malformed stdin must never produce a blocking (exit 2) or crashing exit"


def test_script_exits_0_on_empty_stdin(tmp_path: Path):
    env = {"STEALTHLAB_TRACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input="", capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0


def test_script_exits_0_when_session_id_missing(tmp_path: Path):
    env = {"STEALTHLAB_TRACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0
    assert not any(tmp_path.glob("*.jsonl")), "no session_id means no file should be written"


def test_script_never_writes_to_stdout_that_could_be_misparsed_as_hook_json():
    """Exit-0 stdout is parsed as JSON by Claude Code if present (per
    the documented delivery contract). This wrapper has nothing useful
    to tell Claude Code, so stdout must stay empty -- any accidental
    print() here could be misinterpreted as hookSpecificOutput."""
    env = {"STEALTHLAB_TRACE_DIR": "/tmp", "PATH": "/usr/bin:/bin"}
    payload = {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.stdout == ""
