#!/usr/bin/env python3
"""
Real Claude Code hook wiring for the collector (ticket 16, memory-substrate
map). Invoked as a `type: "command"` hook -- reads the hook's JSON payload
on stdin, maps it to the collector's event shape, and calls
app.services.trace_collector.append_event().

HONEST STATUS: built directly against the real, cited documentation in
.scratch/memory-substrate/research/claude-code-hook-schema.md (fetched
2026-08-17 from https://code.claude.com/docs/en/hooks), the same
discipline every other module in this pipeline uses -- but this script
itself has NOT been run against a real Claude Code process, because none
is available in this sandbox. Every field mapping below that the research
doc did not explicitly confirm is marked inline as an open question, not
silently assumed. Whoever registers this hook for real should treat this
as new, unverified wiring, exactly as trace_collector.py's own docstring
already says about itself.

FAIL-SAFE BY DESIGN, not by accident: per the hook docs (delivery
guarantees section of the research doc), exit code 2 BLOCKS the action
the hook is attached to. A collector that can block the user's actual
tool call because of its own bug would be a far worse failure than
silently losing one event. Every code path below -- malformed stdin, a
missing field, an append_event() failure, anything -- is caught and the
script still exits 0. Diagnostic detail goes to stderr only (visible in
Claude Code's debug log per the docs, never blocking).

Usage (registered in .claude/settings.json, see
scripts/example_hook_settings.json for a real, complete example):

    python3 ${CLAUDE_PROJECT_DIR}/backend/scripts/hook_wrapper.py

Collector output file location: ${CLAUDE_PROJECT_DIR}/.claude/traces/
<session_id>.jsonl by default, overridable via the STEALTHLAB_TRACE_DIR
env var (useful for testing against a scratch directory instead of a
real project's .claude/ folder).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Real, deliberate path manipulation: this script needs
# app.services.trace_collector importable when invoked as a bare
# `python3 .../hook_wrapper.py` (a hook's `command` field is a plain
# script path, not a module invocation) rather than run through the
# backend package's normal entry points.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Event types documented as tool-shaped (event-specific fields section of
# the research doc). Anything else (SessionStart, UserPromptSubmit, Stop,
# etc.) is still recorded, just without assuming tool_name/tool_input/
# tool_output are present -- redact_event()/append_event() already
# tolerate their absence.
_TOOL_SHAPED_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}


def _default_trace_dir(project_dir: str | None) -> Path:
    base = Path(os.environ.get("STEALTHLAB_TRACE_DIR", "")) if os.environ.get(
        "STEALTHLAB_TRACE_DIR"
    ) else None
    if base is not None:
        return base
    root = Path(project_dir) if project_dir else Path.cwd()
    return root / ".claude" / "traces"


def build_event(payload: dict) -> dict:
    """
    Maps one real hook payload (already-parsed JSON) to the collector's
    event shape. Pure function, no I/O -- kept separate from main() so
    it's directly unit-testable against synthetic payloads shaped like
    the documented schema, without needing a real Claude Code process.

    Fields taken directly from the research doc's confirmed common-fields
    list: session_id, hook_event_name, cwd, agent_id (actor_id proxy --
    "unique-id-if-in-subagent"; None means the main agent, not a
    subagent, per the doc's own phrasing).

    OPEN QUESTIONS, not resolved by the available docs (flagged here
    rather than guessed):
    - tool_call_id: no field by this name appears in the documented
      common or tool-specific field lists. Read defensively
      (payload.get) so this becomes a real, correctly-None value rather
      than a KeyError if the real payload doesn't have it either.
    - PostToolUse's success field: not explicitly documented. Inferred
      from the event NAME instead, since PostToolUseFailure is
      documented as a SEPARATE event -- i.e. a PostToolUse firing at all
      implies success, and PostToolUseFailure implies failure. This is
      an inference from the event taxonomy, not a confirmed field.
    - tool_output vs tool_response: the doc explicitly could not
      determine which one the real payload uses ("tool-specific result
      payloads rather than one fixed field name"). Both are read
      defensively; redact_event() (trace_redaction.py) already
      normalizes whichever is present into tool_output.
    - No timestamp field appears in the documented common-fields JSON
      example at all -- this wrapper stamps its own UTC time at
      hook-invocation time, which is "when the collector saw it", not
      necessarily "when Claude Code itself observed it".
    """
    hook_event_name = payload.get("hook_event_name", "Unknown")

    event: dict = {
        "hook_event_name": hook_event_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": payload.get("agent_id"),
    }

    if hook_event_name in _TOOL_SHAPED_EVENTS:
        event["tool_name"] = payload.get("tool_name")
        event["tool_call_id"] = payload.get("tool_call_id")  # open question, see docstring
        if "tool_input" in payload:
            event["tool_input"] = payload["tool_input"]
        if "tool_output" in payload:
            event["tool_output"] = payload["tool_output"]
        if "tool_response" in payload:
            event["tool_response"] = payload["tool_response"]
        if hook_event_name == "PostToolUse":
            event["success"] = True
        elif hook_event_name == "PostToolUseFailure":
            event["success"] = False
            if "error" in payload:
                event["error"] = payload["error"]
    else:
        # Non-tool-shaped event (SessionStart, UserPromptSubmit, Stop,
        # SubagentStart/Stop, etc.) -- record whatever event-specific
        # fields the docs confirm for that name, defensively, without
        # inventing fields the doc doesn't list.
        for key in ("source", "prompt", "reason", "agent_type"):
            if key in payload:
                event[key] = payload[key]

    return event


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception as exc:  # noqa: BLE001 -- fail-safe: never let a
        # stdin read error propagate into a non-zero exit that could be
        # mistaken for exit code 2 (blocking) by a caller that doesn't
        # check the actual code carefully.
        print(f"hook_wrapper: could not read stdin: {exc!r}", file=sys.stderr)
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"hook_wrapper: malformed JSON on stdin: {exc!r}", file=sys.stderr)
        return 0

    try:
        session_id = payload.get("session_id")
        if not session_id:
            print("hook_wrapper: no session_id in payload, dropping event", file=sys.stderr)
            return 0

        # CLAUDE_PROJECT_DIR is documented as an available placeholder/
        # env var for hook commands (configuration mechanism section of
        # the research doc). project_id is a real column now (migration
        # 17) -- this is the concrete value that fills it, falling back
        # to the payload's own cwd if the env var isn't set for some
        # reason (e.g. a hook type other than `command`).
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
        project_id = project_dir

        event = build_event(payload)
        hook_event_name = payload.get("hook_event_name", "Unknown")

        trace_dir = _default_trace_dir(project_dir)
        file_path = trace_dir / f"{session_id}.jsonl"

        from app.services.trace_collector import append_event

        append_event(
            event,
            file_path,
            session_id=session_id,
            event_type=hook_event_name,
            sequence=None,  # real payloads carry no native sequence -- see append_event()'s docstring
        )
        # project_id is not part of the collector's file-level record
        # today (agent_traces/episodes carry it, not trace_events/the
        # collector file) -- intentionally not threaded through here;
        # the worker or a later step would need it if that changes.
        _ = project_id
    except Exception as exc:  # noqa: BLE001 -- fail-safe: a bug in this
        # wrapper must never block the user's actual tool call.
        print(f"hook_wrapper: append_event failed: {exc!r}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
