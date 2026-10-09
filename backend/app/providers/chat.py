"""OpenAI-compatible chat/completions through the governed provider path (the orchestrator surface).

`call_unit` (service.py) serves one prompt and returns one answer. A coding harness (OpenCode, Cline, Codex CLI, ...)
instead sends a whole conversation with tool definitions, needs the model's tool calls back untouched, and usually wants
the reply streamed. This module serves that, and nothing else: it routes the request to a connection that offers the
model, then returns the provider's reply as it is. We do not run the tool loop; the harness owns it.

What is the same as `call_unit`, because it calls the same functions: visibility, data class, the platform egress
policy, the max_cost_usd cap, the endpoint safety check, key rotation and failover between endpoints and models, and the
organisation ledger (reserve the worst case before sending, settle the real cost after, release on failure).

What is different, and why:
  * The fields forwarded are an allowlist (messages, tools, tool_choice, sampling controls, response_format, stop,
    seed). Anything else a client sends is dropped, not forwarded: a harness's private extensions must not become
    instructions to a provider we pay for. `n` other than 1 is refused (it would multiply cost behind one reservation).
  * Streaming asks the provider for `stream_options.include_usage` so the final chunk carries the token counts. If a
    provider does not send usage, the call is settled at its reserved worst case (marked upper_bound), never at zero.
  * A stream can fail after the first byte. Failover happens only BEFORE the first byte; after that the client gets an
    SSE error event and the call is settled on what was observed.

Honest scope limits: this passes through the Chat Completions shape only. It does not translate to or from the OpenAI
Responses API or Anthropic Messages, does not count tokens itself (the provider's usage is trusted, falling back to the
worst case), and has been exercised against fake providers, not against a live one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Sequence

from app.providers import adapters, service
from app.providers.adapters import parse_usage
from app.providers.health import HEALTH
from app.providers.types import (DIRECT, CallRequest, Connection, ProviderCallDenied, ProviderCallFailed, ProviderError,
                                 UnitSpec, unit_of)
from app.services.access import AccessScope
from app.services.classification import DataClass

log = logging.getLogger(__name__)

CHAT_MAX_CHARS = 2_000_000
MAX_MESSAGES = 5000
MAX_TOOLS = 128
DEFAULT_MAX_TOKENS = 4096
ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
# Sampling and format controls that are forwarded as given (type-checked below). Everything not listed is dropped.
_NUMBERS = ("temperature", "top_p", "presence_penalty", "frequency_penalty")
_PASSTHROUGH = ("tool_choice", "parallel_tool_calls", "response_format", "seed", "stop")
_RESPONSE_TEXT_KEEP = 100_000


class ChatRequestInvalid(ProviderError):
    """The request body is not something we will forward (the message says what to fix)."""


@dataclass(frozen=True)
class ChatParams:
    model: str                      # the model name the client asked for
    unit: str                       # model|direct
    body: Mapping[str, Any]         # the forwarded fields, without model / max_tokens / stream
    max_tokens: int
    stream: bool
    prompt_text: str                # the conversation as text, for the worst-case estimate and the cost recorder


def parse_chat_body(raw: Any) -> ChatParams:
    if not isinstance(raw, Mapping):
        raise ChatRequestInvalid("the request body must be a JSON object")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ChatRequestInvalid("model is required")
    model = model.strip()
    if "|" in model and not model.endswith(f"|{DIRECT}"):
        raise ChatRequestInvalid("only direct models are served here (no model|scaffold units)")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ChatRequestInvalid("messages must be a non-empty list")
    if len(messages) > MAX_MESSAGES:
        raise ChatRequestInvalid(f"too many messages (limit {MAX_MESSAGES})")
    for m in messages:
        if not isinstance(m, Mapping) or m.get("role") not in ROLES:
            raise ChatRequestInvalid(f"every message needs a role in {sorted(ROLES)}")
    tools = raw.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or len(tools) > MAX_TOOLS or not all(isinstance(t, Mapping) for t in tools):
            raise ChatRequestInvalid(f"tools must be a list of at most {MAX_TOOLS} objects")
    if raw.get("n") not in (None, 1):
        raise ChatRequestInvalid("n must be 1")
    body: dict[str, Any] = {"messages": messages}
    if tools:
        body["tools"] = tools
    for key in _NUMBERS:
        value = raw.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ChatRequestInvalid(f"{key} must be a number")
            body[key] = value
    for key in _PASSTHROUGH:
        if raw.get(key) is not None:
            body[key] = raw[key]
    cap = raw.get("max_tokens", raw.get("max_completion_tokens"))
    if cap is None:
        cap = DEFAULT_MAX_TOKENS
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= service.MAX_OUTPUT_TOKENS:
        raise ChatRequestInvalid(f"max_tokens must be an integer between 1 and {service.MAX_OUTPUT_TOKENS}")
    try:
        prompt_text = json.dumps(messages, default=str) + (json.dumps(tools, default=str) if tools else "")
    except (TypeError, ValueError) as exc:
        raise ChatRequestInvalid("messages and tools must be plain JSON") from exc
    if len(prompt_text) > CHAT_MAX_CHARS:
        raise ChatRequestInvalid(f"the conversation is longer than {CHAT_MAX_CHARS} characters")
    stream = raw.get("stream")
    if stream is not None and not isinstance(stream, bool):
        raise ChatRequestInvalid("stream must be true or false")
    return ChatParams(model=model, unit=unit_of(model, None), body=body, max_tokens=cap, stream=bool(stream),
                      prompt_text=prompt_text)


@dataclass
class _StreamState:
    usage: Optional[dict] = None
    out_chars: int = 0
    text: list[str] = field(default_factory=list)
    kept: int = 0
    error: Optional[BaseException] = None
    cancelled: bool = False

    def observe(self, payload: str) -> None:
        try:
            obj = json.loads(payload)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            self.usage = obj["usage"]
        for choice in obj.get("choices") or ():
            delta = (choice or {}).get("delta") or {}
            pieces = [delta.get("content")] + [
                ((tc or {}).get("function") or {}).get("arguments") for tc in (delta.get("tool_calls") or ())]
            for piece in pieces:
                if isinstance(piece, str) and piece:
                    self.out_chars += len(piece)
                    if self.kept < _RESPONSE_TEXT_KEEP:
                        self.text.append(piece)
                        self.kept += len(piece)


@dataclass
class ChatOutcome:
    unit: str
    requested_unit: str
    connection_id: str
    provider: str
    model: str
    json: Optional[dict] = None                         # non-streaming: the provider's reply, as received
    stream: Optional[AsyncIterator[str]] = None         # streaming: SSE lines, ready to send
    usage: dict = field(default_factory=dict)           # non-streaming: tokens and cost; streaming: set when it ends
    fell_back: list = field(default_factory=list)


OnDone = Callable[[str, str, str, str], Awaitable[None]]      # (prompt_text, response_text, provider, model)


async def chat(pool: Any, scope: AccessScope, params: ChatParams, *, fallback_models: Sequence[str] = (),
               actor: Optional[str], tenant_id: Optional[str], org_id: Optional[str], governed: bool,
               data_class: str = "USER_PRIVATE", max_cost_usd: Optional[float] = None,
               tool: str = "chat_completions", on_done: Optional[OnDone] = None) -> ChatOutcome:
    """Serve one chat-completions request. Tries the requested model, then `fallback_models` in order, but only when
    every endpoint of the earlier model is down or refused; a request the provider says is bad stops at once."""
    if data_class not in {c.value for c in DataClass}:
        raise ProviderCallDenied(f"unknown data_class {data_class!r}")
    order = list(dict.fromkeys([params.unit, *[unit_of(m, None) for m in fallback_models if m and m.strip()]]))
    fell_back: list[dict[str, Any]] = []
    for position, unit in enumerate(order):
        try:
            outcome = await _chat_unit(pool, scope, unit, params, actor=actor, tenant_id=tenant_id, org_id=org_id,
                                       governed=governed, data_class=data_class, max_cost_usd=max_cost_usd, tool=tool,
                                       on_done=on_done)
        except ProviderCallFailed as exc:
            if not exc.outage or position == len(order) - 1:
                raise
            fell_back.append({"unit": unit, "error": str(exc)})
            continue
        except ProviderCallDenied as exc:
            if position == len(order) - 1:
                raise
            fell_back.append({"unit": unit, "refused": str(exc)})
            continue
        outcome.requested_unit, outcome.fell_back = params.unit, fell_back
        return outcome
    raise ProviderCallFailed("no model could answer: " + json.dumps(fell_back))   # unreachable: the loop returns or raises


async def _chat_unit(pool, scope, unit, params, *, actor, tenant_id, org_id, governed, data_class, max_cost_usd, tool,
                     on_done) -> ChatOutcome:
    offers = await service.offering_connections(scope, unit)
    if not offers:
        raise ProviderCallDenied(f"no connection available to you offers {unit!r}")
    attempts: list[dict[str, Any]] = []
    first_denial: Optional[ProviderCallDenied] = None
    for conn, spec in service._rotation(offers):
        try:
            outcome = await _start_one(pool, scope, unit, conn, spec, params, actor=actor, tenant_id=tenant_id,
                                       org_id=org_id, governed=governed, data_class=data_class,
                                       max_cost_usd=max_cost_usd, tool=tool, on_done=on_done)
        except ProviderCallDenied as exc:
            first_denial = first_denial or exc
            attempts.append({"connection_id": conn.connection_id, "refused": str(exc)})
            continue
        except ProviderCallFailed as exc:
            if not exc.outage:
                raise
            HEALTH.record_failure(conn.connection_id, spec.unit, status=exc.status, transport=exc.transport,
                                  error=str(exc))
            attempts.append({"connection_id": conn.connection_id, "error": str(exc)})
            continue
        HEALTH.record_success(conn.connection_id, spec.unit)
        return outcome
    if first_denial is not None and not any("error" in a for a in attempts):
        raise first_denial
    raise ProviderCallFailed(
        f"every endpoint offering {unit!r} failed: " + "; ".join(a.get("error") or a.get("refused", "") for a in attempts),
        transport=True, attempts=attempts)


def _body_for(conn: Connection, spec: UnitSpec, params: ChatParams, cap: int) -> dict[str, Any]:
    body: dict[str, Any] = {**params.body, "model": spec.sent_model, "max_tokens": cap}
    for key, value in conn.request_extras.items():               # ours always win: extras only ADD fields
        body.setdefault(key, value)
    if params.stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


async def _start_one(pool, scope, unit, conn: Connection, spec: UnitSpec, params: ChatParams, *, actor, tenant_id,
                     org_id, governed, data_class, max_cost_usd, tool, on_done) -> ChatOutcome:
    if conn.kind != "openai_compatible":
        raise ProviderCallDenied(f"connection {conn.connection_id!r} is not an OpenAI-compatible endpoint")
    started = time.monotonic()
    cap = min(params.max_tokens, spec.max_output_tokens or params.max_tokens)
    estimate = CallRequest(prompt=params.prompt_text, max_tokens=cap, data_class=data_class)
    await service.pre_checks(pool, unit, conn, spec, estimate, actor=actor, tenant_id=tenant_id,
                             max_cost_usd=max_cost_usd)
    reservation, gate_ms = await service.hold_budget(
        pool, scope, unit, conn, spec, estimate, actor=actor, org_id=org_id, governed=governed, tool=tool,
        instance_key=None, started=started)
    body = _body_for(conn, spec, params, cap)
    url = adapters._join(conn.base_url, spec, "chat/completions")
    try:
        if params.stream:
            async def open_(secret):
                return await _open_stream(conn, url, body, secret)
            opened = await service.with_keys(conn, spec, open_)
        else:
            async def post(secret):
                headers = {"Authorization": f"Bearer {secret}"} if secret else {}
                return adapters._json(conn, await adapters._post(conn, url, json=body, headers=headers, secret=secret),
                                      secret)
            data = await service.with_keys(conn, spec, post)
    except BaseException as exc:
        if reservation is not None:
            await service.release_hold(pool, reservation, exc)
        raise

    base = dict(unit=unit, requested_unit=unit, connection_id=conn.connection_id, provider=conn.provider,
                model=spec.model)
    if not params.stream:
        latency_ms = int((time.monotonic() - started) * 1000) - (gate_ms or 0)
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            if reservation is not None:
                await service.release_hold(pool, reservation, ProviderCallFailed("unexpected reply shape"))
            raise ProviderCallFailed(f"{conn.connection_id}: unexpected reply shape")
        tin, tout, cached, cost, source = parse_usage(data.get("usage"), spec)
        if reservation is not None:
            await service.settle_hold(pool, reservation, spec, tokens_in=tin, tokens_cache_read=cached,
                                      tokens_cache_write=None, tokens_out=tout, cost_usd=cost, cost_source=source,
                                      latency_ms=latency_ms, gate_ms=gate_ms)
        await _notify(on_done, params.prompt_text, _reply_text(data), conn.provider, spec.model)
        return ChatOutcome(**base, json=data, usage={"tokens_in": tin, "tokens_out": tout, "tokens_cache_read": cached,
                                                      "cost_usd": cost, "cost_source": source, "latency_ms": latency_ms})

    outcome = ChatOutcome(**base)
    state = _StreamState()

    async def finalize() -> None:
        latency_ms = int((time.monotonic() - started) * 1000) - (gate_ms or 0)
        tin = tout = cached = cost = source = None
        if state.usage:
            tin, tout, cached, cost, source = parse_usage(state.usage, spec)
        outcome.usage = {"tokens_in": tin, "tokens_out": tout, "tokens_cache_read": cached, "cost_usd": cost,
                         "cost_source": source, "latency_ms": latency_ms, "completed": state.error is None and not state.cancelled}
        try:
            if reservation is not None:
                if state.error is not None and state.out_chars == 0 and state.usage is None:
                    await service.release_hold(pool, reservation, state.error)      # nothing was produced
                else:
                    await service.settle_hold(pool, reservation, spec, tokens_in=tin, tokens_cache_read=cached,
                                              tokens_cache_write=None, tokens_out=tout, cost_usd=cost,
                                              cost_source=source, latency_ms=latency_ms, gate_ms=gate_ms)
            await _notify(on_done, params.prompt_text, "".join(state.text), conn.provider, spec.model)
        except Exception:  # noqa: BLE001 - the client already has its bytes; record the failure and move on
            log.exception("could not settle streamed chat call %s", unit)

    async def relay() -> AsyncIterator[str]:
        try:
            async for line in opened.response.aiter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload and payload != "[DONE]":
                        state.observe(payload)
                yield line + "\n"
        except (asyncio.CancelledError, GeneratorExit):
            state.cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001 - the upstream dropped mid-stream
            state.error = exc
            message = json.dumps({"error": {"message": "the upstream stream ended early", "type": "upstream_error"}})
            yield f"data: {message}\n\n"
        finally:
            await opened.close()
            await asyncio.shield(finalize())

    outcome.stream = relay()
    return outcome


async def _notify(on_done: Optional[OnDone], prompt_text: str, response_text: str, provider: str, model: str) -> None:
    if on_done is None:
        return
    try:
        await on_done(prompt_text, response_text, provider, model)
    except Exception:  # noqa: BLE001 - recording must never break the answer
        log.exception("chat on_done callback failed")


def _reply_text(data: Mapping[str, Any]) -> str:
    out: list[str] = []
    for choice in data.get("choices") or ():
        msg = (choice or {}).get("message") or {}
        if isinstance(msg.get("content"), str):
            out.append(msg["content"])
        for tc in msg.get("tool_calls") or ():
            args = ((tc or {}).get("function") or {}).get("arguments")
            if isinstance(args, str):
                out.append(args)
    return "".join(out)[:_RESPONSE_TEXT_KEEP]


@dataclass
class _Opened:
    client: Any
    response: Any

    async def close(self) -> None:
        try:
            await self.response.aclose()
        finally:
            await self.client.aclose()


async def _open_stream(conn: Connection, url: str, body: dict, secret: Optional[str]) -> _Opened:
    """Send the request and return once the response HEADERS are in. A refusal or an outage is raised here, before any
    byte reaches the client, so it can still fail over and still return a proper HTTP status."""
    headers = {"Accept": "text/event-stream", **({"Authorization": f"Bearer {secret}"} if secret else {})}
    client = adapters.http_client(timeout=conn.timeout_s or adapters.TIMEOUT_S, follow_redirects=False)
    try:
        response = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
    except Exception as exc:  # noqa: BLE001 - any transport failure is one error to the caller
        await client.aclose()
        raise ProviderCallFailed(f"{conn.connection_id}: request failed ({exc.__class__.__name__})", transport=True) from exc
    if response.status_code >= 300:
        try:
            text = (await response.aread()).decode("utf-8", "replace")
        finally:
            await response.aclose()
            await client.aclose()
        raise ProviderCallFailed(f"{conn.connection_id}: HTTP {response.status_code}: {adapters._snippet(text, secret)}",
                                 status=response.status_code)
    return _Opened(client, response)


async def list_models(scope: AccessScope) -> list[dict[str, Any]]:
    """The models this caller can ask for, in the OpenAI `/v1/models` shape, with what the deployment knows about each
    (prices as declared, data-handling declarations, tier). Only direct units are listed."""
    from app.providers import registry

    seen: dict[str, dict[str, Any]] = {}
    for conn in await registry.visible_connections(scope):
        for u in conn.units:
            if u.scaffold != DIRECT or u.model in seen:
                continue
            seen[u.model] = {
                "id": u.model, "object": "model", "owned_by": conn.provider,
                "stealthlab": {"tier": u.tier, "input_per_mtok": u.input_per_mtok, "output_per_mtok": u.output_per_mtok,
                               "max_output_tokens": u.max_output_tokens, "connection": conn.connection_id,
                               "compliance": conn.compliance}}
    return list(seen.values())
