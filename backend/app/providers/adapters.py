"""Adapters: one per kind of endpoint. Each turns a CallRequest into one HTTP exchange.

  openai_compatible  POST {base}/chat/completions -- the shape most hosts share (General Compute,
                     Together, Fireworks, OpenRouter, Groq, vLLM, ...). Vertex, Anthropic-native,
                     Bedrock and Azure are NOT this shape; they need their own adapter (register one
                     with `register_adapter`).
  a2a                an Agent2Agent endpoint, so an AI agent *with its harness* is callable like a
                     model. Speaks JSON-RPC `SendMessage` (A2A 1.0) and falls back to the 0.x
                     `message/send` when the agent answers "method not found". It sends one message and
                     reads the reply; it does not poll a long-running task or stream (returns the task
                     state so the caller can see that it is not finished). Written from the published
                     spec, not yet exercised against a live agent.

Adapters never read secrets themselves (the service passes one in), never follow redirects, and keep
the credential out of every error message.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional, Protocol

from app.providers.types import CallRequest, CallResult, Connection, ProviderCallFailed, UnitSpec, tokens_cost

TIMEOUT_S = 120.0
_SNIPPET = 300


def http_client(**kwargs):
    """The shared-TLS httpx client (app/utils/tls.py). Tests replace this to inject a MockTransport."""
    from app.utils.tls import async_http_client

    return async_http_client(**kwargs)


class Adapter(Protocol):
    async def call(self, conn: Connection, spec: UnitSpec, request: CallRequest, secret: Optional[str]) -> CallResult: ...


def _snippet(text: str, secret: Optional[str]) -> str:
    text = (text or "")[:_SNIPPET]
    return text.replace(secret, "[redacted]") if secret else text


def _join(base: str, spec: UnitSpec, default_path: str) -> str:
    path = spec.path or default_path
    return base.rstrip("/") + ("" if not path else "/" + path.lstrip("/"))


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def _post(conn: Connection, url: str, *, json: dict, headers: dict, secret: Optional[str]):
    try:
        async with http_client(timeout=conn.timeout_s or TIMEOUT_S, follow_redirects=False) as client:
            return await client.post(url, json=json, headers=headers)
    except Exception as exc:  # noqa: BLE001 -- any transport failure is one error to the caller
        raise ProviderCallFailed(f"{conn.connection_id}: request failed ({exc.__class__.__name__})",
                                 transport=True) from exc


def _json(conn: Connection, response, secret: Optional[str]) -> Any:
    if response.status_code >= 300:
        raise ProviderCallFailed(f"{conn.connection_id}: HTTP {response.status_code}: {_snippet(response.text, secret)}",
                                 status=response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderCallFailed(f"{conn.connection_id}: the endpoint did not return JSON") from exc


# ------------------------------------------------------------------ openai_compatible

class OpenAICompatAdapter:
    async def call(self, conn, spec, request, secret):
        cap = min(request.max_tokens, spec.max_output_tokens or request.max_tokens)
        messages = ([{"role": "system", "content": request.system}] if request.system else []) + [
            {"role": "user", "content": request.prompt}]
        body: dict[str, Any] = {"model": spec.sent_model, "messages": messages, "max_tokens": cap}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        t0 = time.monotonic()
        data = _json(conn, await _post(conn, _join(conn.base_url, spec, "chat/completions"), json=body,
                                       headers=headers, secret=secret), secret)
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderCallFailed(f"{conn.connection_id}: unexpected reply shape") from exc
        if isinstance(content, list):                                  # content-part arrays
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text, finish = content or "", choice.get("finish_reason")
        if not text and finish == "length":
            raise ProviderCallFailed(f"{conn.connection_id}: empty reply (finish_reason=length): the model used its "
                                     "token budget before answering; raise max_tokens")
        usage = data.get("usage") or {}
        prompt, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        # The OpenAI shape counts cached tokens INSIDE prompt_tokens and reports them as a subset; split them so
        # `tokens_in` is always fresh input (Anthropic-style endpoints already report them separately).
        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                  if isinstance(usage.get("prompt_tokens_details"), dict) else None)
        cached = cached if isinstance(cached, int) and cached >= 0 else None
        tokens_in = prompt if prompt is None or cached is None else max(prompt - cached, 0)
        reported = usage.get("cost")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0:
            cost, source = float(reported), "provider"
        else:
            cost = tokens_cost(spec, tokens_in, tokens_out, cached)
            source = "declared" if cost is not None else None
        return CallResult(unit=spec.unit, connection_id=conn.connection_id, text=text, tokens_in=tokens_in,
                          tokens_out=tokens_out, tokens_cache_read=cached, cost_usd=cost, cost_source=source,
                          latency_ms=_ms(t0), finish_reason=finish)


# ------------------------------------------------------------------ a2a

_OK_STATES = {"completed"}
_FAILED_STATES = {"failed", "rejected", "canceled", "cancelled"}


def _state(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.removeprefix("TASK_STATE_").lower().replace("-", "_")


def _parts_text(parts: Any) -> str:
    out = []
    for part in parts or []:
        if isinstance(part, dict):
            piece = part.get("text")
            if piece is None and isinstance(part.get("root"), dict):    # some 0.x SDK serialisations
                piece = part["root"].get("text")
            if piece:
                out.append(str(piece))
    return "\n".join(out)


def _reply_text(payload: dict) -> tuple[str, Optional[str]]:
    """(text, task state) from a Task or a Message, in either spec generation."""
    task = payload.get("task") if isinstance(payload.get("task"), dict) else None
    message = payload.get("message") if isinstance(payload.get("message"), dict) else None
    if task is None and message is None:
        task = payload if "status" in payload else None
        message = payload if "parts" in payload else None
    if task is not None:
        status = task.get("status") or {}
        texts = [_parts_text(a.get("parts")) for a in task.get("artifacts") or [] if isinstance(a, dict)]
        text = "\n".join(t for t in texts if t) or _parts_text((status.get("message") or {}).get("parts"))
        return text, _state(status.get("state"))
    return _parts_text((message or {}).get("parts")), None


class A2AAdapter:
    async def call(self, conn, spec, request, secret):
        prompt = request.prompt if not request.system else f"{request.system}\n\n{request.prompt}"
        headers = {"A2A-Version": "1.0", **({"Authorization": f"Bearer {secret}"} if secret else {})}
        url = _join(conn.base_url, spec, "")
        t0 = time.monotonic()
        data = _json(conn, await _post(conn, url, headers=headers, secret=secret, json={
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
            "params": {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": prompt}]}}}),
            secret)
        if isinstance(data.get("error"), dict) and data["error"].get("code") == -32601:     # a 0.x agent
            data = _json(conn, await _post(conn, url, headers={k: v for k, v in headers.items() if k != "A2A-Version"},
                                           secret=secret, json={
                "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
                "params": {"message": {"messageId": str(uuid.uuid4()), "role": "user",
                                       "parts": [{"kind": "text", "text": prompt}]}}}), secret)
        if isinstance(data.get("error"), dict):
            raise ProviderCallFailed(f"{conn.connection_id}: agent error {data['error'].get('code')}: "
                                     f"{_snippet(str(data['error'].get('message')), secret)}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ProviderCallFailed(f"{conn.connection_id}: the agent returned no result")
        text, state = _reply_text(result)
        if state in _FAILED_STATES:
            raise ProviderCallFailed(f"{conn.connection_id}: the agent's task ended {state}")
        cost = tokens_cost(spec, None, None)
        return CallResult(unit=spec.unit, connection_id=conn.connection_id, text=text, cost_usd=cost,
                          cost_source="declared" if cost is not None else None, latency_ms=_ms(t0),
                          state=None if state in (None, *_OK_STATES) else state)


ADAPTERS: dict[str, Adapter] = {"openai_compatible": OpenAICompatAdapter(), "a2a": A2AAdapter()}


def register_adapter(kind: str, adapter: Adapter) -> None:
    ADAPTERS[kind] = adapter
