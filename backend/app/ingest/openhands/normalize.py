"""One nebius OpenHands trajectory row (OpenAI-style chat messages) -> a core `NormalizedTrajectory`.

Row shape (verified on the pinned revision, 2026-09-29): `trajectory` is a list of messages with `role` in
system | user | assistant | tool. An assistant message carries its reasoning in `content` and its actions in
`tool_calls` (`function.name`, `function.arguments` as a JSON string, `id`); each tool message answers one call
through `tool_call_id`. Tools: execute_bash, str_replace_editor, think, task_tracker, finish.

Events (one per action, so a whole run stays one episode -- the core splits episodes only above 200 events, and a
run is capped at 100 turns):
  * the first user message (the issue)       -> OBSERVE  tool "task"
  * later user messages                       -> OBSERVE  tool "user_message"
  * each tool call + its result               -> by tool:
        execute_bash                          -> TEST when the command runs a test runner, else EXECUTE
        str_replace_editor view               -> READ;  create | str_replace | insert | undo_edit -> WRITE
        think | task_tracker                  -> REASON
        finish                                -> COMMIT
        anything else                         -> no canonical type (never guessed)
    `tool_input` is the parsed arguments plus the assistant's `reasoning` text; `tool_output` is the result text;
    `success` comes from the bash exit-code line (other tools: unknown).
  * an assistant message with no tool call    -> REASON   tool "assistant_message"
  * the system prompt is OpenHands boilerplate and is not an event (its sha256 is kept in the metadata).

Anything that does not fit -- arguments that are not JSON, a result with no call, a call with no result -- is
counted in `skipped_malformed_events` and makes the whole trajectory malformed when it breaks the pairing:
a trajectory is kept whole or not at all.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.ingestion_sources.normalized_trajectory import NormalizedEvent, NormalizedTrajectory

PROVIDER = "swe-rebench-openhands"
PROVIDER_VERSION = "openhands-0.54.0"
MODEL = "Qwen/Qwen3-Coder-480B-A35B-Instruct"
MAX_REASONING_CHARS = 4000

_EXIT = re.compile(r"\[(?:The command completed|Command finished) with exit code (-?\d+)\.?\]")
_TEST_RUNNER = re.compile(
    r"(^|[\s;&|(])(pytest|py\.test|python[0-9.]*\s+-m\s+(pytest|unittest)|tox|nox|nosetests|"
    r"npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|"
    r"make\s+test|rspec|phpunit|jest|vitest|mocha)\b")
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)   # synthetic, strictly increasing: the export has no timestamps


class MalformedTrajectory(ValueError):
    """The row cannot be turned into a faithful trajectory. The message names the defect."""


def _canonical(tool: str, args: dict) -> Optional[str]:
    if tool == "execute_bash":
        return "TEST" if _TEST_RUNNER.search(str(args.get("command") or "")) else "EXECUTE"
    if tool == "str_replace_editor":
        cmd = args.get("command")
        if cmd == "view":
            return "READ"
        if cmd in ("create", "str_replace", "insert", "undo_edit"):
            return "WRITE"
        return None
    if tool in ("think", "task_tracker"):
        return "REASON"
    if tool == "finish":
        return "COMMIT"
    return None


def _success(tool: str, output: str) -> Optional[bool]:
    if tool != "execute_bash":
        return None
    codes = _EXIT.findall(output or "")
    return (int(codes[-1]) == 0) if codes else None


def issue_text(first_user_message: str) -> str:
    """The issue inside OpenHands' wrapper, or the whole message when there is no wrapper."""
    m = re.search(r"<issue_description>\s*(.*?)\s*</issue_description>", first_user_message or "", re.S)
    return (m.group(1) if m else (first_user_message or "")).strip()


def normalize(row: dict[str, Any], *, dataset: str, revision: str) -> NormalizedTrajectory:
    trajectory_id = str(row.get("trajectory_id") or "")
    messages = row.get("trajectory") or []
    if not trajectory_id or not messages:
        raise MalformedTrajectory("row has no trajectory_id or no messages")

    events: list[NormalizedEvent] = []
    pending: dict[str, tuple[int, str, dict, str]] = {}   # call id -> (slot, tool, args, reasoning)
    slots: list[Optional[NormalizedEvent]] = []
    system_sha = None
    seen_task = False

    def add(tool: str, canonical: Optional[str], tool_input: dict, tool_output: Optional[dict],
            success: Optional[bool], raw: dict) -> None:
        seq = len(slots)
        slots.append(NormalizedEvent(
            sequence=seq, canonical_event_type=canonical, tool_name=tool, tool_input=tool_input,
            tool_output=tool_output, raw_event=raw, success=success,
            dedup_key=f"{PROVIDER}:{trajectory_id}:{seq}", timestamp=_EPOCH + timedelta(seconds=seq)))

    for index, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            system_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            continue
        if role == "user":
            add("task" if not seen_task else "user_message", "OBSERVE",
                {"issue": issue_text(content)} if not seen_task else {"message": content},
                None, None, {"message_index": index, "role": "user"})
            seen_task = True
            continue
        if role == "assistant":
            calls = msg.get("tool_calls") or []
            if not calls:
                add("assistant_message", "REASON", {"reasoning": content[:MAX_REASONING_CHARS]}, None, None,
                    {"message_index": index, "role": "assistant"})
                continue
            for call in calls:
                fn = call.get("function") or {}
                tool = str(fn.get("name") or "")
                call_id = str(call.get("id") or "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError) as exc:
                    raise MalformedTrajectory(f"message {index}: tool call arguments are not JSON ({exc})") from exc
                if not tool or not call_id or not isinstance(args, dict):
                    raise MalformedTrajectory(f"message {index}: tool call without a name, id or object arguments")
                if call_id in pending:
                    raise MalformedTrajectory(f"message {index}: duplicate tool call id {call_id}")
                slot = len(slots)
                slots.append(None)          # filled when the result arrives, so order is the call order
                pending[call_id] = (slot, tool, args, content[:MAX_REASONING_CHARS])
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            if call_id not in pending:
                raise MalformedTrajectory(f"message {index}: tool result for an unknown call {call_id!r}")
            slot, tool, args, reasoning = pending.pop(call_id)
            slots[slot] = NormalizedEvent(
                sequence=slot, canonical_event_type=_canonical(tool, args), tool_name=tool,
                tool_input={**args, **({"reasoning": reasoning} if reasoning else {})},
                tool_output={"text": content}, raw_event={"message_index": index, "tool_call_id": call_id},
                success=_success(tool, content), dedup_key=f"{PROVIDER}:{trajectory_id}:{slot}",
                timestamp=_EPOCH + timedelta(seconds=slot))
            continue
        raise MalformedTrajectory(f"message {index}: unknown role {role!r}")

    # A call left without a result is legitimate ONLY for the final `finish` action, which OpenHands does not answer.
    for call_id, (slot, tool, args, reasoning) in pending.items():
        if tool != "finish":
            raise MalformedTrajectory(f"tool call {call_id} ({tool}) has no result")
        slots[slot] = NormalizedEvent(
            sequence=slot, canonical_event_type="COMMIT", tool_name=tool,
            tool_input={**args, **({"reasoning": reasoning} if reasoning else {})}, tool_output=None,
            raw_event={"tool_call_id": call_id}, success=None, dedup_key=f"{PROVIDER}:{trajectory_id}:{slot}",
            timestamp=_EPOCH + timedelta(seconds=slot))
    if not seen_task:
        raise MalformedTrajectory("no user message: the task is missing")
    events = [e for e in slots if e is not None]
    if len(events) != len(slots):
        raise MalformedTrajectory("an action slot was never filled")

    resolved = int(row.get("resolved") or 0) == 1
    patch = row.get("model_patch") or ""
    task = next(e.tool_input["issue"] for e in events if e.tool_name == "task")
    return NormalizedTrajectory(
        trace_id=f"{PROVIDER}:{trajectory_id}",
        session_id=f"{PROVIDER}:{trajectory_id}",
        provider=PROVIDER,
        provider_version=PROVIDER_VERSION,
        events=events,
        model=MODEL,
        outcome="success" if resolved else "failure",
        metadata={
            "dataset": dataset, "revision": revision, "trajectory_id": trajectory_id,
            "instance_id": row.get("instance_id"), "repo": row.get("repo"), "resolved": resolved,
            "exit_status": row.get("exit_status"), "declared_goal": task,
            "model_patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest() if patch else None,
            "model_patch_bytes": len(patch.encode("utf-8")), "system_prompt_sha256": system_sha,
        },
    )
