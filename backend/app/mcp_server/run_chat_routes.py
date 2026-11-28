"""The model endpoint for the agent inside a website run's sandbox (app/execution/run_sandbox.py).

    POST /v1/runs/{run_id}/chat/completions     OpenAI Chat Completions, streamed or not
    GET  /v1/runs/{run_id}/models               the models the run's agent is configured with

Who may call: only the run's own token (app/execution/run_token.py), and only while that run is `running`. A person's
session token is refused here, and a run token is refused everywhere else, so the credential that sits inside a
stranger-prompted sandbox opens one door: model calls for one run, while it runs.

It is the same governed provider path as the website's /v1/chat/completions (app/providers/chat.py), paid the way a
headless run's in-process steps are paid (the run owner's scope, not an organisation's), and every call is recorded
against the run and its current step in `llm_spend`, so measure_run prices a sandboxed run like any other.

Limits, per run (in this process): KELL_RUN_CHAT_MAX_CALLS calls (default 600) and KELL_RUN_CHAT_PER_MIN a minute
(default 60). The body cap is larger than the website's (an agent's conversation carries its tool results): 6 MB.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger(__name__)

MAX_BODY_BYTES = int(os.environ.get("KELL_RUN_CHAT_MAX_BODY", str(6 * 1024 * 1024)))
MAX_CALLS = int(os.environ.get("KELL_RUN_CHAT_MAX_CALLS", "600"))
PER_MIN = int(os.environ.get("KELL_RUN_CHAT_PER_MIN", "60"))
TOOL_NAME = "run_way_agent"

_calls: dict[str, int] = defaultdict(int)
_recent: dict[str, deque] = defaultdict(deque)


def _err(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _throttled(run_id: str) -> Optional[str]:
    now = time.monotonic()
    q = _recent[run_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= PER_MIN:
        return "too many model calls this minute for this run"
    if _calls[run_id] >= MAX_CALLS:
        return f"this run has made {MAX_CALLS} model calls, the most one run may make"
    q.append(now)
    _calls[run_id] += 1
    return None


async def authorise(pool: Any, request: Request, run_id: str) -> tuple[Optional[dict], Optional[tuple[int, str]]]:
    """-> ({run_id, owner_id, step}, None) or (None, (status, why)). The token, then the run's row."""
    from app.execution import run_token

    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not run_token.is_run_token(token):
        return None, (401, "this endpoint takes a run's own token")
    try:
        claims = run_token.verify(token)
    except run_token.RunTokenInvalid as exc:
        return None, (401, str(exc))
    if claims.run_id != run_id:
        return None, (403, "this token belongs to another run")
    if pool is None:
        return None, (503, "the server is starting; retry shortly")
    row = await pool.fetchrow("SELECT owner_id, status, next_step FROM way_runs WHERE id = $1::uuid", run_id)
    if row is None or str(row["owner_id"]) != claims.owner_id:
        return None, (403, "no such run")
    if row["status"] != "running":
        return None, (403, f"this run is {row['status']}; its token no longer works")
    return {"run_id": run_id, "owner_id": claims.owner_id, "step": int(row["next_step"] or 0)}, None


async def _attribute(pool: Any, run: dict, provider: str, model: str, usage: dict, prompt: str, reply: str) -> None:
    """One llm_spend row per call, against the run and the step it is on (the measurement measure_run reads)."""
    tin = usage.get("tokens_in")
    tout = usage.get("tokens_out")
    if tin is None:
        tin = len(prompt) // 4                # the provider sent no usage: an estimate, never zero
    if tout is None:
        tout = len(reply) // 4
    try:
        await pool.execute(
            "INSERT INTO llm_spend (scope_key, provider, model, operation, estimated_cost, input_tokens, "
            "output_tokens, run_id, step_order) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::uuid,$9)",
            "way_run", provider or "unknown", model or "unknown", "way_step_agent",
            float(usage.get("cost_usd") or 0.0), int(tin or 0) + int(usage.get("tokens_cache_read") or 0),
            int(tout or 0), run["run_id"], run["step"])
        await pool.execute("UPDATE way_runs SET updated_at = now() WHERE id = $1::uuid", run["run_id"])
    except Exception:  # noqa: BLE001 -- the call already happened; log loudly, never fail the agent
        log.error("could not attribute a sandbox model call to run %s", run["run_id"], exc_info=True)


def register(server: Any, pool_getter: Callable[[], Any]) -> None:

    @server.custom_route("/v1/runs/{run_id}/models", methods=["GET"], include_in_schema=False)
    async def models(request: Request) -> Response:
        run, refusal = await authorise(pool_getter(), request, request.path_params["run_id"])
        if refusal:
            return _err(refusal[0], refusal[1], "authentication_error" if refusal[0] == 401 else "permission_error")
        from app.execution import run_sandbox

        ids = list(dict.fromkeys([run_sandbox.DEFAULT_MODEL, *run_sandbox.FALLBACK_MODELS]))
        return JSONResponse({"object": "list", "data": [{"id": m, "object": "model"} for m in ids]})

    @server.custom_route("/v1/runs/{run_id}/chat/completions", methods=["POST"], include_in_schema=False)
    async def completions(request: Request) -> Response:
        pool = pool_getter()
        run, refusal = await authorise(pool, request, request.path_params["run_id"])
        if refusal:
            return _err(refusal[0], refusal[1], "authentication_error" if refusal[0] == 401 else "permission_error")
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return _err(413, "the request body is too large")
        why = _throttled(run["run_id"])
        if why:
            return _err(429, why, "rate_limit_error")
        try:
            raw = await request.json()
        except ValueError:
            return _err(400, "the request body is not valid JSON")
        from app.mcp_server.chat_routes import _failure, fallback_models
        from app.providers import chat as _chat
        from app.providers.types import ProviderCallDenied, ProviderCallFailed
        from app.services.access import AccessScope

        holder: dict[str, Any] = {}

        async def on_done(prompt_text: str, response_text: str, provider: str, model: str) -> None:
            usage = dict(getattr(holder.get("outcome"), "usage", None) or {})
            await _attribute(pool, run, provider, model, usage, prompt_text, response_text)

        try:
            params = _chat.parse_chat_body(raw)
            outcome = await _chat.chat(
                pool, AccessScope.for_user(run["owner_id"]), params,
                fallback_models=fallback_models(request.headers.get("x-stealthlab-fallback-models")),
                actor=run["owner_id"], tenant_id=None, org_id=None, governed=False,
                data_class=request.headers.get("x-stealthlab-data-class") or "USER_PRIVATE",
                tool=TOOL_NAME, on_done=on_done)
        except (_chat.ChatRequestInvalid, ProviderCallDenied, ProviderCallFailed) as exc:
            return _failure(request, exc)
        holder["outcome"] = outcome
        headers = {"x-stealthlab-unit": outcome.unit, "x-stealthlab-ai-generated": "true"}
        if outcome.stream is not None:
            headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return StreamingResponse(outcome.stream, media_type="text/event-stream", headers=headers)
        headers["Cache-Control"] = "no-store"
        return JSONResponse(outcome.json, headers=headers)
