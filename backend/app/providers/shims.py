"""Translating the Anthropic Messages and OpenAI Responses APIs to and from Chat Completions.

Why: the governed provider path (providers/chat.py) speaks Chat Completions, because that is what the hosted open-model
providers speak. Claude Code talks Anthropic Messages (ANTHROPIC_BASE_URL) and Codex CLI talks the OpenAI Responses API.
These functions let both use a routed model through the same path, with the same policy, budget and ledger, instead of
each vendor's endpoint directly.

Covered: text, function/tool calls and their results, system prompts, tool choice, stop sequences, token usage,
non-streaming replies and streaming events.
Not covered, and refused or dropped rather than faked: images and files, extended thinking / reasoning items (dropped),
server-side tools such as web search (dropped; only custom/function tools are forwarded), previous_response_id and other
server-held state (refused: the Responses shim is stateless), `n` > 1, and prompt-cache markers (ignored).
These shapes are written from the vendors' published API references and exercised against fakes and, for Messages, one
local run of the real client; they have not been run against every client version.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Mapping, Optional

from app.providers.chat import ChatRequestInvalid


def _text_of(content: Any) -> str:
    """Plain text of a content value that may be a string or a list of blocks."""
    if isinstance(content, str):
        return content
    out: list[str] = []
    for block in content or ():
        if isinstance(block, str):
            out.append(block)
        elif isinstance(block, Mapping):
            if isinstance(block.get("text"), str):
                out.append(block["text"])
            elif block.get("type") in ("image", "input_image", "image_url"):
                out.append("[image omitted]")
    return "\n".join(out)


def _function_tool(name: Any, description: Any, parameters: Any) -> dict[str, Any]:
    fn: dict[str, Any] = {"name": name, "parameters": parameters if isinstance(parameters, Mapping) else {"type": "object", "properties": {}}}
    if isinstance(description, str) and description:
        fn["description"] = description
    return {"type": "function", "function": fn}


# ---------------------------------------------------------------------------------------- Anthropic Messages

def anthropic_to_chat(raw: Any) -> dict[str, Any]:
    """An Anthropic Messages request as a Chat Completions request body (validated further by parse_chat_body)."""
    if not isinstance(raw, Mapping):
        raise ChatRequestInvalid("the request body must be a JSON object")
    if not isinstance(raw.get("max_tokens"), int) or isinstance(raw.get("max_tokens"), bool):
        raise ChatRequestInvalid("max_tokens is required")
    messages: list[dict[str, Any]] = []
    system = raw.get("system")
    if system:
        messages.append({"role": "system", "content": _text_of(system)})
    in_line_system: list[str] = []
    for m in raw.get("messages") or ():
        if not isinstance(m, Mapping) or m.get("role") not in ("user", "assistant", "system"):
            raise ChatRequestInvalid("every message needs a role of user, assistant or system")
        content = m.get("content")
        if m["role"] == "system":
            # Claude Code puts system text inside `messages` as well as in `system` (seen against the real client).
            # Open models' chat templates often accept a system turn only first, so these are hoisted to the front.
            if _text_of(content).strip():
                in_line_system.append(_text_of(content))
            continue
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content or ())
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in blocks if isinstance(b, Mapping) and b.get("type") == "text")
            calls = [{"id": b.get("id"), "type": "function",
                      "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input") or {})}}
                     for b in blocks if isinstance(b, Mapping) and b.get("type") == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            if text or calls:
                messages.append(msg)
        else:
            texts: list[str] = []
            for b in blocks:
                if not isinstance(b, Mapping):
                    continue
                if b.get("type") == "tool_result":
                    body = _text_of(b.get("content"))
                    messages.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                                     "content": ("error: " + body) if b.get("is_error") else body})
                elif b.get("type") in ("text", "image"):
                    texts.append(_text_of([b]))
            if texts:
                messages.append({"role": "user", "content": "\n".join(texts)})
    if in_line_system:
        at = 1 if messages and messages[0]["role"] == "system" else 0
        messages[at:at] = [{"role": "system", "content": "\n\n".join(in_line_system)}]
    body: dict[str, Any] = {"model": raw.get("model"), "messages": messages, "max_tokens": raw["max_tokens"],
                            "stream": bool(raw.get("stream"))}
    tools = [_function_tool(t.get("name"), t.get("description"), t.get("input_schema"))
             for t in raw.get("tools") or () if isinstance(t, Mapping) and t.get("name") and "input_schema" in t]
    if tools:
        body["tools"] = tools
    choice = raw.get("tool_choice")
    if isinstance(choice, Mapping) and tools:
        kind = choice.get("type")
        if kind == "auto":
            body["tool_choice"] = "auto"
        elif kind == "any":
            body["tool_choice"] = "required"
        elif kind == "none":
            body["tool_choice"] = "none"
        elif kind == "tool" and choice.get("name"):
            body["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    for key in ("temperature", "top_p"):
        if raw.get(key) is not None:
            body[key] = raw[key]
    if raw.get("stop_sequences"):
        body["stop"] = list(raw["stop_sequences"])[:4]
    return body


_STOP = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
         "content_filter": "end_turn"}


def _tool_input(arguments: Any) -> Any:
    try:
        value = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def chat_to_anthropic(data: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    choice = (data.get("choices") or [{}])[0] or {}
    msg = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    if isinstance(msg.get("content"), str) and msg["content"]:
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or ():
        fn = (tc or {}).get("function") or {}
        content.append({"type": "tool_use", "id": (tc or {}).get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": fn.get("name"), "input": _tool_input(fn.get("arguments"))})
    usage = data.get("usage") or {}
    return {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "model": model,
            "content": content or [{"type": "text", "text": ""}],
            "stop_reason": _STOP.get(choice.get("finish_reason"), "end_turn"), "stop_sequence": None,
            "usage": {"input_tokens": int(usage.get("prompt_tokens") or 0), "output_tokens": int(usage.get("completion_tokens") or 0)}}


async def chunks(stream: AsyncIterator[str]) -> AsyncIterator[Optional[dict]]:
    """Parsed `data:` payloads of a Chat Completions SSE stream; None for the [DONE] marker."""
    async for line in stream:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            yield None
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def chat_stream_to_anthropic(stream: AsyncIterator[str], *, model: str, input_tokens: int) -> AsyncIterator[str]:
    """Chat Completions SSE in, Anthropic Messages SSE out. The final usage chunk of a chat stream arrives after the
    finish_reason chunk, so message_delta is held back until the stream ends."""
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    started = False
    index = -1
    open_kind: Optional[str] = None
    tool_blocks: dict[int, int] = {}
    finish: Optional[str] = None
    usage: dict = {}

    def close() -> list[str]:
        nonlocal open_kind
        out = [_sse("content_block_stop", {"type": "content_block_stop", "index": index})] if open_kind else []
        open_kind = None
        return out

    async for chunk in chunks(stream):
        if chunk is None:
            continue
        if "error" in chunk:
            for part in close():
                yield part
            yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": str((chunk["error"] or {}).get("message", "upstream error"))}})
            return
        if not started:
            started = True
            yield _sse("message_start", {"type": "message_start", "message": {
                "id": message_id, "type": "message", "role": "assistant", "model": model, "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}})
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or ():
            delta = (choice or {}).get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                if open_kind != "text":
                    for part in close():
                        yield part
                    index += 1
                    open_kind = "text"
                    yield _sse("content_block_start", {"type": "content_block_start", "index": index,
                                                       "content_block": {"type": "text", "text": ""}})
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": index,
                                                   "delta": {"type": "text_delta", "text": delta["content"]}})
            for tc in delta.get("tool_calls") or ():
                slot = int((tc or {}).get("index") or 0)
                fn = (tc or {}).get("function") or {}
                if slot not in tool_blocks:
                    for part in close():
                        yield part
                    index += 1
                    open_kind = "tool"
                    tool_blocks[slot] = index
                    yield _sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": {
                        "type": "tool_use", "id": (tc or {}).get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": fn.get("name") or "", "input": {}}})
                if isinstance(fn.get("arguments"), str) and fn["arguments"]:
                    yield _sse("content_block_delta", {"type": "content_block_delta", "index": tool_blocks[slot],
                                                       "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    if not started:
        yield _sse("message_start", {"type": "message_start", "message": {
            "id": message_id, "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}})
    for part in close():
        yield part
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": _STOP.get(finish, "end_turn"), "stop_sequence": None},
                                 "usage": {"input_tokens": int(usage.get("prompt_tokens") or input_tokens),
                                           "output_tokens": int(usage.get("completion_tokens") or 0)}})
    yield _sse("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------------------- OpenAI Responses

def responses_to_chat(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ChatRequestInvalid("the request body must be a JSON object")
    if raw.get("previous_response_id"):
        raise ChatRequestInvalid("previous_response_id is not supported: this endpoint is stateless, send the full input")
    messages: list[dict[str, Any]] = []
    if raw.get("instructions"):
        messages.append({"role": "system", "content": _text_of(raw["instructions"])})
    items = raw.get("input")
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    if not isinstance(items, list) or not items:
        raise ChatRequestInvalid("input must be a string or a non-empty list")
    for item in items:
        if not isinstance(item, Mapping):
            raise ChatRequestInvalid("every input item must be an object")
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = item.get("role")
            role = "system" if role == "developer" else role
            if role not in ("system", "user", "assistant"):
                raise ChatRequestInvalid("message items need a role of system, developer, user or assistant")
            text = _text_of(item.get("content"))
            if role == "assistant" and messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls") and not text:
                continue
            messages.append({"role": role, "content": text})
        elif kind == "function_call":
            call = {"id": item.get("call_id") or item.get("id"), "type": "function",
                    "function": {"name": item.get("name"), "arguments": item.get("arguments") or "{}"}}
            if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            out = item.get("output")
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"),
                             "content": out if isinstance(out, str) else json.dumps(out)})
        # reasoning items and anything else the model produced are not replayed
    body: dict[str, Any] = {"model": raw.get("model"), "messages": messages, "stream": bool(raw.get("stream"))}
    if raw.get("max_output_tokens") is not None:
        body["max_tokens"] = raw["max_output_tokens"]
    tools = [_function_tool(t.get("name"), t.get("description"), t.get("parameters"))
             for t in raw.get("tools") or () if isinstance(t, Mapping) and t.get("type") == "function" and t.get("name")]
    if tools:
        body["tools"] = tools
    choice = raw.get("tool_choice")
    if tools and choice is not None:
        if choice in ("auto", "required", "none"):
            body["tool_choice"] = choice
        elif isinstance(choice, Mapping) and choice.get("type") == "function" and choice.get("name"):
            body["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    for key in ("temperature", "top_p"):
        if raw.get(key) is not None:
            body[key] = raw[key]
    if raw.get("parallel_tool_calls") is not None:
        body["parallel_tool_calls"] = raw["parallel_tool_calls"]
    return body


def _usage_responses(usage: Mapping[str, Any]) -> dict[str, Any]:
    tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens") if isinstance(usage.get("prompt_tokens_details"), dict) else 0) or 0
    return {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout,
            "input_tokens_details": {"cached_tokens": int(cached)}, "output_tokens_details": {"reasoning_tokens": 0}}


def _response_object(response_id: str, model: str, created: int, status: str, output: list, usage: Optional[Mapping] = None) -> dict:
    obj = {"id": response_id, "object": "response", "created_at": created, "status": status, "error": None, "model": model,
           "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "store": False}
    if usage is not None:
        obj["usage"] = _usage_responses(usage)
    return obj


def _message_item(item_id: str, text: str, status: str = "completed") -> dict:
    return {"id": item_id, "type": "message", "status": status, "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}] if status == "completed" or text else []}


def _call_item(item_id: str, call_id: str, name: str, arguments: str, status: str = "completed") -> dict:
    return {"id": item_id, "type": "function_call", "status": status, "call_id": call_id, "name": name, "arguments": arguments}


def chat_to_responses(data: Mapping[str, Any], *, model: str, now: int) -> dict[str, Any]:
    msg = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    output: list[dict] = []
    if isinstance(msg.get("content"), str) and msg["content"]:
        output.append(_message_item(f"msg_{uuid.uuid4().hex[:24]}", msg["content"]))
    for tc in msg.get("tool_calls") or ():
        fn = (tc or {}).get("function") or {}
        output.append(_call_item(f"fc_{uuid.uuid4().hex[:24]}", (tc or {}).get("id") or f"call_{uuid.uuid4().hex[:20]}",
                                 fn.get("name") or "", fn.get("arguments") or "{}"))
    return _response_object(f"resp_{uuid.uuid4().hex[:24]}", model, now, "completed", output, data.get("usage") or {})


async def chat_stream_to_responses(stream: AsyncIterator[str], *, model: str, now: int) -> AsyncIterator[str]:
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    seq = 0

    def ev(name: str, **fields: Any) -> str:
        nonlocal seq
        payload = {"type": name, "sequence_number": seq, **fields}
        seq += 1
        return f"event: {name}\ndata: {json.dumps(payload)}\n\n"

    yield ev("response.created", response=_response_object(response_id, model, now, "in_progress", []))
    yield ev("response.in_progress", response=_response_object(response_id, model, now, "in_progress", []))
    output: list[dict] = []
    text_item: Optional[dict] = None          # {"id","index","text"}
    calls: dict[int, dict] = {}
    usage: dict = {}

    def close_text() -> list[str]:
        nonlocal text_item
        if text_item is None:
            return []
        item = _message_item(text_item["id"], text_item["text"])
        output.append(item)
        parts = [ev("response.output_text.done", item_id=text_item["id"], output_index=text_item["index"], content_index=0, text=text_item["text"]),
                 ev("response.content_part.done", item_id=text_item["id"], output_index=text_item["index"], content_index=0, part=item["content"][0]),
                 ev("response.output_item.done", output_index=text_item["index"], item=item)]
        text_item = None
        return parts

    def close_calls() -> list[str]:
        parts = []
        for slot in sorted(calls):
            c = calls[slot]
            if c["done"]:
                continue
            c["done"] = True
            item = _call_item(c["id"], c["call_id"], c["name"], c["arguments"])
            output.append(item)
            parts.append(ev("response.function_call_arguments.done", item_id=c["id"], output_index=c["index"], arguments=c["arguments"]))
            parts.append(ev("response.output_item.done", output_index=c["index"], item=item))
        return parts

    next_index = 0
    async for chunk in chunks(stream):
        if chunk is None:
            continue
        if "error" in chunk:
            yield ev("error", code="upstream_error", message=str((chunk["error"] or {}).get("message", "upstream error")), param=None)
            return
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or ():
            delta = (choice or {}).get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                if text_item is None:
                    for part in close_calls():
                        yield part
                    text_item = {"id": f"msg_{uuid.uuid4().hex[:24]}", "index": next_index, "text": ""}
                    next_index += 1
                    yield ev("response.output_item.added", output_index=text_item["index"], item={
                        "id": text_item["id"], "type": "message", "status": "in_progress", "role": "assistant", "content": []})
                    yield ev("response.content_part.added", item_id=text_item["id"], output_index=text_item["index"], content_index=0,
                             part={"type": "output_text", "text": "", "annotations": []})
                text_item["text"] += delta["content"]
                yield ev("response.output_text.delta", item_id=text_item["id"], output_index=text_item["index"], content_index=0, delta=delta["content"])
            for tc in delta.get("tool_calls") or ():
                slot = int((tc or {}).get("index") or 0)
                fn = (tc or {}).get("function") or {}
                if slot not in calls:
                    for part in close_text():
                        yield part
                    calls[slot] = {"id": f"fc_{uuid.uuid4().hex[:24]}", "call_id": (tc or {}).get("id") or f"call_{uuid.uuid4().hex[:20]}",
                                   "name": fn.get("name") or "", "arguments": "", "index": next_index, "done": False}
                    next_index += 1
                    c = calls[slot]
                    yield ev("response.output_item.added", output_index=c["index"], item=_call_item(c["id"], c["call_id"], c["name"], "", "in_progress"))
                if isinstance(fn.get("arguments"), str) and fn["arguments"]:
                    c = calls[slot]
                    c["arguments"] += fn["arguments"]
                    yield ev("response.function_call_arguments.delta", item_id=c["id"], output_index=c["index"], delta=fn["arguments"])
    for part in close_text():
        yield part
    for part in close_calls():
        yield part
    yield ev("response.completed", response=_response_object(response_id, model, now, "completed", output, usage))
