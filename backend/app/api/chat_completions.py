"""The OpenAI-compatible surface: POST /v1/chat/completions and GET /v1/models.

Point a coding harness (OpenCode, Cline, Codex CLI, ...) at `{server}/v1` with a StealthLab bearer token and it can use
every model the caller's connections offer, with tools and streaming, under the organisation's policy and budgets. The
work is in app/providers/chat.py; this module is HTTP only: identity, limits, headers, error shapes.

Per-request controls are headers, because the body is the OpenAI schema and must stay that:
  X-Stealthlab-Org              which organisation pays, when the caller belongs to several
  X-Stealthlab-Data-Class       the sensitivity of the prompt (default USER_PRIVATE, the most conservative private class)
  X-Stealthlab-Max-Cost-Usd     refuse if this single call could cost more than this
  X-Stealthlab-Fallback-Models  comma-separated models to try, in order, if every endpoint of the first is down

The org policy must list the tool name `chat_completions` in allowed_tools, like any other tool.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import enforce_limits, get_auth_context, get_scope, make_cost_recorder, require_scopes
from app.config import settings
from app.providers import chat as _chat
from app.providers.types import ProviderCallDenied, ProviderCallFailed
from app.services import auth_context as _ac
from app.services.access import AccessScope

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

TOOL_NAME = "chat_completions"


def _error(status: int, message: str, kind: str, code: Optional[str] = None, headers: Optional[dict] = None) -> JSONResponse:
    """OpenAI's error envelope, which every client library already parses."""
    return JSONResponse({"error": {"message": message, "type": kind, "code": code}}, status_code=status, headers=headers)


def _failure_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, _chat.ChatRequestInvalid):
        return _error(400, str(exc), "invalid_request_error", "invalid_request")
    if isinstance(exc, ProviderCallDenied):
        text = str(exc)
        if text.startswith("no connection available"):
            return _error(404, text, "invalid_request_error", "model_not_found")
        return _error(403, text, "permission_error", "policy_denied")
    if isinstance(exc, ProviderCallFailed):
        if exc.status == 429:
            return _error(429, str(exc), "rate_limit_error", "upstream_rate_limited")
        if exc.status is not None and 400 <= exc.status < 500 and exc.status not in (401, 402, 403):
            return _error(400, str(exc), "invalid_request_error", "upstream_rejected_request")
        return _error(502, str(exc), "api_error", "upstream_unavailable")
    raise exc


@router.post("/chat/completions", dependencies=[Depends(require_scopes(_ac.EXECUTION_RUN))])
async def chat_completions(
    request: Request,
    scope: AccessScope = Depends(get_scope),
    limit_key: str = Depends(enforce_limits),
    x_stealthlab_org: Optional[str] = Header(default=None),
    x_stealthlab_data_class: Optional[str] = Header(default=None),
    x_stealthlab_max_cost_usd: Optional[float] = Header(default=None),
    x_stealthlab_fallback_models: Optional[str] = Header(default=None),
):
    try:
        raw: Any = await request.json()
    except ValueError:
        return _error(400, "the request body is not valid JSON", "invalid_request_error", "invalid_json")
    try:
        params = _chat.parse_chat_body(raw)
    except _chat.ChatRequestInvalid as exc:
        return _failure_response(exc)

    pool = request.app.state.pool
    ctx = await get_auth_context(request)
    actor = ctx.actor_id
    recorder = make_cost_recorder(pool, limit_key, TOOL_NAME)

    async def on_done(prompt_text: str, response_text: str, provider: str, model: str) -> None:
        await recorder(SimpleNamespace(family=provider, model_id=model), prompt_text, response_text)

    fallbacks = [m.strip() for m in (x_stealthlab_fallback_models or "").split(",") if m.strip()][:5]
    try:
        outcome = await _chat.chat(
            pool, scope, params, fallback_models=fallbacks, actor=actor,
            tenant_id=x_stealthlab_org or (scope.org_ids[0] if scope.org_ids else None), org_id=x_stealthlab_org,
            governed=settings.deployment_mode == "shared", data_class=x_stealthlab_data_class or "USER_PRIVATE",
            max_cost_usd=x_stealthlab_max_cost_usd, tool=TOOL_NAME, on_done=on_done)
    except (ProviderCallDenied, ProviderCallFailed) as exc:
        return _failure_response(exc)

    headers = {"x-stealthlab-unit": outcome.unit, "x-stealthlab-connection": outcome.connection_id}
    if outcome.fell_back:
        headers["x-stealthlab-fell-back"] = ",".join(str(f.get("unit")) for f in outcome.fell_back)
    if outcome.stream is not None:
        headers.update({"cache-control": "no-cache", "x-accel-buffering": "no"})
        return StreamingResponse(outcome.stream, media_type="text/event-stream", headers=headers)
    cost = outcome.usage.get("cost_usd")
    if cost is not None:
        headers["x-stealthlab-cost-usd"] = f"{cost:.6f}"
    return JSONResponse(outcome.json, headers=headers)


@router.get("/models", dependencies=[Depends(require_scopes(_ac.EXECUTION_RUN))])
async def list_models(scope: AccessScope = Depends(get_scope)):
    return {"object": "list", "data": await _chat.list_models(scope)}
