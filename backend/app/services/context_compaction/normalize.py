"""
Normalize harness-specific sessions into ContextItems, and group tool
call+result into judged Units.

Two adapters, so this works across Claude Code / Codex / OpenHands / Cline /
future harnesses without a per-harness compactor:
  * items_from_chat_messages -- OpenAI-style (assistant.tool_calls + role=tool)
    and Anthropic-style (tool_use / tool_result content blocks) message lists.
  * items_from_trace_events  -- the canonical `trace_events` rows
    (tool_call_id / tool_input / tool_output / success / error).

The labels assigned here (file_read / test / command / search) are METADATA for
the semantic judge and for lifecycle guards; they do not decide retention.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from app.services.context_compaction.models import ContextItem, Unit, sha

_FILE_READ_TOOLS = {"read", "read_file", "view", "open", "cat", "str_replace_editor", "readfile"}
_SEARCH_TOOLS = {"grep", "glob", "search", "find", "ripgrep", "web_search", "list_dir", "ls"}
_COMMAND_TOOLS = {"bash", "shell", "run_command", "execute_bash", "exec", "powershell", "terminal", "command"}
_TEST_RE = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (run )?test|pnpm (run )?test|tox)\b", re.I)
_EXIT_RE = re.compile(r"(exit(?:ed)?(?: with)?(?: code| status)?[: ]+)(-?\d+)", re.I)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # content blocks
        parts = []
        for b in value:
            if isinstance(b, dict):
                parts.append(str(b.get("text") or b.get("content") or json.dumps(b, default=str)))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return json.dumps(value, default=str, ensure_ascii=False)


def _path_from_input(tool_input: Any) -> Optional[str]:
    if isinstance(tool_input, dict):
        for k in ("file_path", "path", "filename", "file"):
            if isinstance(tool_input.get(k), str):
                return tool_input[k]
    return None


def _command_from_input(tool_input: Any) -> Optional[str]:
    if isinstance(tool_input, dict):
        for k in ("command", "cmd", "script"):
            if isinstance(tool_input.get(k), str):
                return tool_input[k]
    return None


def classify_result(tool_name: Optional[str], tool_input: Any, output: str, failed: bool) -> str:
    name = (tool_name or "").lower().split("__")[-1]
    cmd = _command_from_input(tool_input) or ""
    if name in _FILE_READ_TOOLS:
        return "file_read"
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _COMMAND_TOOLS or cmd:
        return "test" if _TEST_RE.search(cmd) else "command"
    return "error" if failed else "tool_result"


def looks_failed(output: str, explicit: Optional[bool] = None, error: Optional[str] = None) -> bool:
    if error:
        return True
    if explicit is False:
        return True
    m = _EXIT_RE.search(output[-400:] if output else "")
    if m and int(m.group(2)) != 0:
        return True
    return bool(re.search(r"^(FAILED|ERROR)\b|Traceback \(most recent call last\)", output or "", re.M))


def items_from_chat_messages(messages: Iterable[dict], *, id_prefix: str = "m") -> list[ContextItem]:
    items: list[ContextItem] = []
    calls: dict[str, dict] = {}
    seq = 0

    def add(kind, text, **kw):
        nonlocal seq
        seq += 1
        items.append(ContextItem(item_id=f"{id_prefix}{seq}", kind=kind, text=text, sequence=seq, **kw))

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            add("system", _text(content))
        elif role == "user":
            blocks = content if isinstance(content, list) else None
            handled = False
            for b in blocks or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":  # Anthropic style
                    _add_result(add, calls, b.get("tool_use_id"), _text(b.get("content")), b.get("is_error"))
                    handled = True
            if not handled:
                add("user_message", _text(content))
        elif role == "assistant":
            text = _text(content) if not isinstance(content, list) else _text(
                [b for b in content if isinstance(b, dict) and b.get("type") == "text"])
            if text.strip():
                add("assistant_message", text)
            for tc in msg.get("tool_calls") or []:  # OpenAI style
                fn = tc.get("function", {})
                args = fn.get("arguments")
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except json.JSONDecodeError:
                    pass
                _add_call(add, calls, tc.get("id"), fn.get("name"), args)
            for b in content if isinstance(content, list) else []:  # Anthropic style
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    _add_call(add, calls, b.get("id"), b.get("name"), b.get("input"))
        elif role == "tool":
            _add_result(add, calls, msg.get("tool_call_id"), _text(content), None)
    return items


def _add_call(add, calls, call_id, name, args):
    calls[str(call_id)] = {"name": name, "input": args}
    add("tool_call", f"{name}({_text(args)})", tool_name=name, call_id=str(call_id),
        path=_path_from_input(args))


def _add_result(add, calls, call_id, output, is_error):
    info = calls.get(str(call_id), {})
    failed = looks_failed(output, None if not is_error else False)
    kind = classify_result(info.get("name"), info.get("input"), output, failed)
    path = _path_from_input(info.get("input"))
    add(kind, output, tool_name=info.get("name"), call_id=str(call_id), path=path, failed=failed,
        content_hash=sha(output) if kind == "file_read" else None)


def items_from_trace_events(rows: Iterable[dict]) -> list[ContextItem]:
    """Canonical trace_events rows (already ordered by sequence).

    One trace row usually carries BOTH tool_input and tool_output, so it becomes
    a tool_call + result PAIR (same call_id, same `raw_ref` = the trace event
    id) -- that is what lets a downstream citation of a compacted unit resolve
    back to the exact raw event(s)."""
    items: list[ContextItem] = []
    for r in rows:
        seq = int(r.get("sequence") or len(items))
        raw_ref = str(r.get("id")) if r.get("id") is not None else None
        et = str(r.get("event_type") or "")
        tool, cid = r.get("tool_name"), r.get("tool_call_id")
        tin, tout = r.get("tool_input"), r.get("tool_output")
        if tool:
            call_id = str(cid) if cid else f"e{seq}"
            path = _path_from_input(tin)
            items.append(ContextItem(f"e{seq}c", "tool_call", f"{tool}({_text(tin)})", seq, tool, call_id,
                                     path, None, False, raw_ref))
            if tout is not None or r.get("error") or r.get("success") is not None:
                out = _text(tout)
                failed = looks_failed(out, r.get("success"), r.get("error"))
                if r.get("error"):
                    out = f"{out}\n{r['error']}".strip()
                kind = classify_result(tool, tin, out, failed)
                items.append(ContextItem(f"e{seq}r", kind, out, seq, tool, call_id, path,
                                         sha(out) if kind == "file_read" else None, failed, raw_ref))
        elif "user" in et:
            items.append(ContextItem(f"e{seq}", "user_message", _text(r.get("payload") or tin), seq, raw_ref=raw_ref))
        elif "error" in et or r.get("error"):
            items.append(ContextItem(f"e{seq}", "error", _text(r.get("error") or tout), seq, failed=True, raw_ref=raw_ref))
        else:
            items.append(ContextItem(f"e{seq}", "status", _text(r.get("payload") or tin or et), seq, raw_ref=raw_ref))
    return items

def group_units(items: list[ContextItem]) -> list[Unit]:
    """Pair each tool_call with its result(s) by call_id; everything else is a
    single-item unit. Order follows the first item of each unit. An orphan
    call or result stays a single-item unit."""
    by_call: dict[str, list[ContextItem]] = {}
    for it in items:
        if it.call_id and it.kind != "assistant_message":
            by_call.setdefault(it.call_id, []).append(it)
    units: list[Unit] = []
    seen: set[str] = set()
    for it in items:
        if it.item_id in seen:
            continue
        group = by_call.get(it.call_id) if it.call_id else None
        if group and len(group) > 1 and any(g.kind == "tool_call" for g in group):
            group = sorted(group, key=lambda g: (g.kind != "tool_call", g.sequence))
            seen.update(g.item_id for g in group)
            units.append(Unit(unit_id=group[0].item_id, items=group))
        else:
            seen.add(it.item_id)
            units.append(Unit(unit_id=it.item_id, items=[it]))
    return units
