"""The OpenAI- and Anthropic-compatible surface over the governed provider connections.

  POST /v1/chat/completions       OpenAI Chat Completions (OpenCode, Cline, most harnesses)
  POST /v1/responses              OpenAI Responses API, stateless (Codex CLI)
  POST /v1/messages               Anthropic Messages (Claude Code via ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN)
  POST /v1/messages/count_tokens  a rough estimate, free of charge
  GET  /v1/models                 the models the caller may use

All of them end in one place, app/providers/chat.py, so the policy, budget, ledger, failover and data-class checks are
the same whichever shape a client speaks. The Messages and Responses shapes are translated by app/providers/shims.py.
This module is HTTP only: identity, limits, headers, error envelopes.

Point a client at `{server}/v1` with a StealthLab bearer token (Anthropic clients: ANTHROPIC_AUTH_TOKEN, which is sent as
`Authorization: Bearer`; a key sent only as `x-api-key` is not read).

Per-request controls are headers, because the bodies are the vendors' schemas and must stay that:
  X-Stealthlab-Org              which organisation pays, when the caller belongs to several
  X-Stealthlab-Data-Class       the sensitivity of the prompt (default USER_PRIVATE, the most conservative private class)
  X-Stealthlab-Max-Cost-Usd     refuse if this single call could cost more than this
  X-Stealthlab-Fallback-Models  comma-separated models to try, in order, if every endpoint of the first is down

The org policy must list the tool name `chat_completions` in allowed_tools, like any other tool.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import enforce_limits, get_auth_context, get_scope, make_cost_recorder, require_scopes
from app.config import settings
from app.providers import chat as _chat
from app.providers import shims
from app.providers.types import ProviderCallDenied, ProviderCallFailed, estimate_tokens
from app.services import auth_context as _ac
from app.services.access import AccessScope

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

TOOL_NAME = "chat_completions"


@dataclass(frozen=True)
class Controls:
    org: Optional[str]
    data_class: str
    max_cost_usd: Optional[float]
    fallbacks: list


def _controls(
    x_stealthlab_org: Optional[str] = Header(default=None),
    x_stealthlab_data_class: Optional[str] = Header(default=None),
    x_stealthlab_max_cost_usd: Optional[float] = Header(default=None),
    x_stealthlab_fallback_models: Optional[str] = Header(default=None),
) -> Controls:
    return Controls(org=x_stealthlab_org, data_class=x_stealthlab_data_class or "USER_PRIVATE",
                    max_cost_usd=x_stealthlab_max_cost_usd,
                    fallbacks=[m.strip() for m in (x_stealthlab_fallback_models or "").split(",") if m.strip()][:5])


def _openai_error(status: int, message: str, kind: str, code: Optional[str] = None) -> JSONResponse:
    """OpenAI's error envelope, which every client library already parses."""
    return JSONResponse({"error": {"message": message, "type": kind, "code": code}}, status_code=status)


def _anthropic_error(status: int, message: str, kind: str, code: Optional[str] = None) -> JSONResponse:
    kinds = {"invalid_request_error": "invalid_request_error", "permission_error": "permission_error",
             "rate_limit_error": "rate_limit_error", "api_error": "api_error"}
    if status == 404:
        kinds["invalid_request_error"] = "not_found_error"
    return JSONResponse({"type": "error", "error": {"type": kinds.get(kind, "api_error"), "message": message}},
                        status_code=status)


Fail = Callable[[int, str, str, Optional[str]], JSONResponse]


def _failure_response(exc: Exception, fail: Fail) -> JSONResponse:
    if isinstance(exc, _chat.ChatRequestInvalid):
        return fail(400, str(exc), "invalid_request_error", "invalid_request")
    if isinstance(exc, ProviderCallDenied):
        text = str(exc)
        if text.startswith("no connection available"):
            return fail(404, text, "invalid_request_error", "model_not_found")
        return fail(403, text, "permission_error", "policy_denied")
    if isinstance(exc, ProviderCallFailed):
        if exc.status == 429:
            return fail(429, str(exc), "rate_limit_error", "upstream_rate_limited")
        if exc.status is not None and 400 <= exc.status < 500 and exc.status not in (401, 402, 403):
            return fail(400, str(exc), "invalid_request_error", "upstream_rejected_request")
        return fail(502, str(exc), "api_error", "upstream_unavailable")
    raise exc


async def _json_body(request: Request, fail: Fail):
    try:
        return await request.json(), None
    except ValueError:
        return None, fail(400, "the request body is not valid JSON", "invalid_request_error", "invalid_json")


async def _run(request: Request, scope: AccessScope, limit_key: str, ctl: Controls, chat_body: Any, fail: Fail):
    """Validate the Chat Completions-shaped body and serve it. Returns (params, outcome, None) or (None, None, error)."""
    try:
        params = _chat.parse_chat_body(chat_body)
    except _chat.ChatRequestInvalid as exc:
        return None, None, _failure_response(exc, fail)
    pool = request.app.state.pool
    ctx = await get_auth_context(request)
    recorder = make_cost_recorder(pool, limit_key, TOOL_NAME)

    async def on_done(prompt_text: str, response_text: str, provider: str, model: str) -> None:
        await recorder(SimpleNamespace(family=provider, model_id=model), prompt_text, response_text)

    try:
        outcome = await _chat.chat(
            pool, scope, params, fallback_models=ctl.fallbacks, actor=ctx.actor_id,
            tenant_id=ctl.org or (scope.org_ids[0] if scope.org_ids else None), org_id=ctl.org,
            governed=settings.deployment_mode == "shared", data_class=ctl.data_class, max_cost_usd=ctl.max_cost_usd,
            tool=TOOL_NAME, on_done=on_done)
    except (ProviderCallDenied, ProviderCallFailed) as exc:
        return None, None, _failure_response(exc, fail)
    return params, outcome, None


def _headers(outcome: _chat.ChatOutcome, *, stream: bool) -> dict:
    headers = {"x-stealthlab-unit": outcome.unit, "x-stealthlab-connection": outcome.connection_id}
    if outcome.fell_back:
        headers["x-stealthlab-fell-back"] = ",".join(str(f.get("unit")) for f in outcome.fell_back)
    if stream:
        headers.update({"cache-control": "no-cache", "x-accel-buffering": "no"})
    elif outcome.usage.get("cost_usd") is not None:
        headers["x-stealthlab-cost-usd"] = f"{outcome.usage['cost_usd']:.6f}"
    return headers


_DEP = [Depends(require_scopes(_ac.EXECUTION_RUN))]


@router.post("/chat/completions", dependencies=_DEP)
async def chat_completions(request: Request, scope: AccessScope = Depends(get_scope),
                           limit_key: str = Depends(enforce_limits), ctl: Controls = Depends(_controls)):
    raw, error = await _json_body(request, _openai_error)
    if error is not None:
        return error
    _, outcome, error = await _run(request, scope, limit_key, ctl, raw, _openai_error)
    if error is not None:
        return error
    if outcome.stream is not None:
        return StreamingResponse(outcome.stream, media_type="text/event-stream", headers=_headers(outcome, stream=True))
    return JSONResponse(outcome.json, headers=_headers(outcome, stream=False))


@router.post("/messages", dependencies=_DEP)
async def anthropic_messages(request: Request, scope: AccessScope = Depends(get_scope),
                             limit_key: str = Depends(enforce_limits), ctl: Controls = Depends(_controls)):
    raw, error = await _json_body(request, _anthropic_error)
    if error is not None:
        return error
    try:
        chat_body = shims.anthropic_to_chat(raw)
    except _chat.ChatRequestInvalid as exc:
        return _failure_response(exc, _anthropic_error)
    params, outcome, error = await _run(request, scope, limit_key, ctl, chat_body, _anthropic_error)
    if error is not None:
        return error
    if outcome.stream is not None:
        body = shims.chat_stream_to_anthropic(outcome.stream, model=params.model,
                                              input_tokens=estimate_tokens(params.prompt_text))
        return StreamingResponse(body, media_type="text/event-stream", headers=_headers(outcome, stream=True))
    return JSONResponse(shims.chat_to_anthropic(outcome.json, model=params.model), headers=_headers(outcome, stream=False))


@router.post("/messages/count_tokens", dependencies=_DEP)
async def anthropic_count_tokens(request: Request):
    """A rough estimate (about 3.5 characters a token), the same one the cost caps use. Costs nothing and calls nobody."""
    raw, error = await _json_body(request, _anthropic_error)
    if error is not None:
        return error
    try:
        if not isinstance(raw, dict):
            raise _chat.ChatRequestInvalid("the request body must be a JSON object")
        params = _chat.parse_chat_body(shims.anthropic_to_chat({**raw, "max_tokens": raw.get("max_tokens") or 1}))
    except _chat.ChatRequestInvalid as exc:
        return _failure_response(exc, _anthropic_error)
    return {"input_tokens": estimate_tokens(params.prompt_text)}


@router.post("/responses", dependencies=_DEP)
async def openai_responses(request: Request, scope: AccessScope = Depends(get_scope),
                           limit_key: str = Depends(enforce_limits), ctl: Controls = Depends(_controls)):
    raw, error = await _json_body(request, _openai_error)
    if error is not None:
        return error
    try:
        chat_body = shims.responses_to_chat(raw)
    except _chat.ChatRequestInvalid as exc:
        return _failure_response(exc, _openai_error)
    params, outcome, error = await _run(request, scope, limit_key, ctl, chat_body, _openai_error)
    if error is not None:
        return error
    now = int(time.time())
    if outcome.stream is not None:
        body = shims.chat_stream_to_responses(outcome.stream, model=params.model, now=now)
        return StreamingResponse(body, media_type="text/event-stream", headers=_headers(outcome, stream=True))
    return JSONResponse(shims.chat_to_responses(outcome.json, model=params.model, now=now), headers=_headers(outcome, stream=False))


@router.get("/models", dependencies=_DEP)
async def list_models(scope: AccessScope = Depends(get_scope)):
    return {"object": "list", "data": await _chat.list_models(scope)}
