from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ._bootstrap import BackendRootNotFound, get_backend_root

_TOOL_SHAPED_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}


def default_trace_file(payload: dict, trace_dir: str | os.PathLike | None = None,
                       file_path: str | os.PathLike | None = None) -> Path | None:
    session_id = payload.get("session_id")
    if not session_id:
        return None
    if file_path:
        return Path(file_path)
    base: Path
    if trace_dir:
        base = Path(trace_dir)
    elif os.environ.get("STEALTHLAB_TRACE_DIR"):
        base = Path(os.environ["STEALTHLAB_TRACE_DIR"])
    else:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
        base = (Path(project_dir) if project_dir else Path.cwd()) / ".claude" / "traces"
    return base / f"{session_id}.jsonl"


def build_event(payload: dict) -> dict:
    hook_event_name = payload.get("hook_event_name", "Unknown")
    event: dict = {
        "hook_event_name": hook_event_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": payload.get("agent_id"),
    }
    if hook_event_name in _TOOL_SHAPED_EVENTS:
        event["tool_name"] = payload.get("tool_name")
        event["tool_call_id"] = payload.get("tool_call_id")
        for key in ("tool_input", "tool_output", "tool_response"):
            if key in payload:
                event[key] = payload[key]
        if hook_event_name == "PostToolUse":
            event["success"] = True
        elif hook_event_name == "PostToolUseFailure":
            event["success"] = False
            if "error" in payload:
                event["error"] = payload["error"]
    else:
        for key in ("source", "prompt", "reason", "agent_type"):
            if key in payload:
                event[key] = payload[key]
    return event


def collect_payload(payload: dict, trace_dir=None, file_path=None):
    target = default_trace_file(payload, trace_dir=trace_dir, file_path=file_path)
    if target is None:
        return None
    try:
        get_backend_root()
    except BackendRootNotFound as exc:
        raise RuntimeError(
            f"{exc} -- the trace hook needs the StealthLab backend checkout on disk "
            f"to import app.services.trace_collector"
        ) from exc
    from app.services.trace_collector import append_event

    return append_event(
        build_event(payload),
        target,
        session_id=payload["session_id"],
        event_type=payload.get("hook_event_name", "Unknown"),
        sequence=None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealthlab-trace-hook",
        description="Claude Code hook command: read one hook JSON payload on stdin, "
        "redact and append it to the local StealthLab collector file. Always exits 0 "
        "-- a collector failure must never block the agent's tool call.",
    )
    parser.add_argument("--file", default=None,
                        help="explicit collector .jsonl path (overrides --trace-dir/env/cwd defaults)")
    parser.add_argument("--trace-dir", default=None,
                        help="directory for <session_id>.jsonl files (default: $CLAUDE_PROJECT_DIR/.claude/traces)")
    parser.add_argument("--backend-root", default=None,
                        help="path to the StealthLab backend checkout (default: $STEALTHLAB_BACKEND_ROOT or auto-discovery)")
    parser.add_argument("--verbose", action="store_true",
                        help="print the written record's dedup_key/sequence to stderr on success")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read()
    except Exception as exc:
        print(f"stealthlab-trace-hook: could not read stdin: {exc!r}", file=sys.stderr)
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"stealthlab-trace-hook: malformed JSON on stdin: {exc!r}", file=sys.stderr)
        return 0

    try:
        record = collect_payload(payload, trace_dir=args.trace_dir, file_path=args.file)
        if record is None:
            print("stealthlab-trace-hook: no session_id in payload, dropping event",
                  file=sys.stderr)
            return 0
        if args.verbose:
            print(f"stealthlab-trace-hook: wrote dedup_key={record['dedup_key']} "
                  f"sequence={record['sequence']}", file=sys.stderr)
    except Exception as exc:
        print(f"stealthlab-trace-hook: append failed: {exc!r}", file=sys.stderr)
        return 0

    return 0
