"""
The OpenAI-compatible chat surface on the hosted MCP service, for the kell website:

  POST /v1/chat/completions   OpenAI Chat Completions, optionally streamed (Server-Sent Events)
  GET  /v1/models             the direct models this person may use

Why here. The same router exists on the full API app (app/api/chat_completions.py), but the hosted deployment runs only the
MCP service, so the site had nothing to call for kell's own pilot models. This is a thin HTTP layer over the same governed
path (app/providers/chat.py): org policy, allow-lists, data classes, per-user daily and organization monthly dollar caps,
the platform egress policy and the ledger all apply exactly as they do for the `call_model` tool. Nothing here widens them.

Who may call: a signed-in person's Supabase WEBSITE-SESSION token (same rule as /v1/delegate; see delegate_routes.py for why
MCP-flow tokens are not accepted here). The person must hold the `execution:run` scope, which every signed-in user gets.

Which organization pays: the caller names one with X-Stealthlab-Org, which must be one they belong to. When they send none
and the pilot is configured (KELL_PILOT_ORG_ID), the pilot organization pays, so a person in several organizations still
gets the capped pilot without choosing. Otherwise the governed path decides (one membership -> that one; several -> refused
with a message asking them to name one).

Data class: X-Stealthlab-Data-Class, default USER_PRIVATE (the most conservative), which the pilot policy refuses. The
pilot models take PUBLIC_SOURCE, PUBLIC_DERIVED and GLOBAL_PROCEDURE only.

Abuse limits (in this process only; the MCP service runs one worker): per client address 120 requests a minute before the
token is even checked, per signed-in person 20 a minute, and one request body at most 256 KB. They stand beside the dollar
caps, which stay the real control. Every answer carries `x-stealthlab-ai-generated: true`, so a client can label it as
machine-written (Z.ai's terms ask end users to be told). This surface is for kell's own apps and signed-in people, not a
public pass-through to the providers: do not hand its token to third parties.

Honest limits: the counters reset on restart and are not shared across machines, and a streamed answer is charged on the
provider's usage when the stream ends, so a client that drops mid-stream may be charged an estimate (chat.py decides).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.mcp_server.delegate_routes import allowed_origins

import os
import time
from collections import defaultdict, deque

ALLOWED_HEADERS = "authorization, content-type, x-stealthlab-data-class, x-stealthlab-org, x-stealthlab-max-cost-usd"
EXPOSED_HEADERS = "x-stealthlab-unit, x-stealthlab-connection, x-stealthlab-cost-usd, x-stealthlab-ai-generated"
MAX_BODY_BYTES = 256 * 1024


class Window:
    """A sliding-window counter per key. In memory: enough for one worker, not a distributed limiter."""

    def __init__(self, limit: int, seconds: float = 60.0) -> None:
        self.limit, self.seconds, self._hits = limit, seconds, defaultdict(deque)

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        while q and now - q[0] >= self.seconds:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 20000:                       # forget idle keys so the table cannot grow without bound
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.seconds][:5000]:
                self._hits.pop(k, None)
        return True


BY_ADDRESS = Window(int(os.environ.get("KELL_CHAT_PER_IP_PER_MIN", "120")))
BY_PERSON = Window(int(os.environ.get("KELL_CHAT_PER_USER_PER_MIN", "20")))


def client_address(request: Request) -> str:
    """The caller's address; behind Railway's proxy that is the first X-Forwarded-For entry."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if getattr(request, "client", None) else "unknown")
TOOL_NAME = "chat_completions"


def cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    if origin and origin in allowed_origins():
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Headers": ALLOWED_HEADERS,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Expose-Headers": EXPOSED_HEADERS,
                "Access-Control-Max-Age": "600"}
    return {"Vary": "Origin"}


def _error(request: Request, status: int, message: str, kind: str, code: Optional[str] = None) -> JSONResponse:
    """OpenAI's error envelope, which every client library already parses."""
    return JSONResponse({"error": {"message": message, "type": kind, "code": code}}, status_code=status,
                        headers={**cors_headers(request), "Cache-Control": "no-store"})


def _kind(status: int) -> str:
    return {401: "authentication_error", 429: "rate_limit_error"}.get(status, "api_error")


def _failure(request: Request, exc: Exception) -> JSONResponse:
    from app.providers import chat as _chat
    from app.providers.types import ProviderCallDenied, ProviderCallFailed

    if isinstance(exc, _chat.ChatRequestInvalid):
        return _error(request, 400, str(exc), "invalid_request_error", "invalid_request")
    if isinstance(exc, ProviderCallDenied):
        text = str(exc)
        if text.startswith("no connection available"):
            return _error(request, 404, text, "invalid_request_error", "model_not_found")
        return _error(request, 403, text, "permission_error", "policy_denied")
    if isinstance(exc, ProviderCallFailed):
        if exc.status == 429:
            return _error(request, 429, str(exc), "rate_limit_error", "upstream_rate_limited")
        if exc.status is not None and 400 <= exc.status < 500 and exc.status not in (401, 402, 403):
            return _error(request, 400, str(exc), "invalid_request_error", "upstream_rejected_request")
        return _error(request, 502, str(exc), "api_error", "upstream_unavailable")
    raise exc


class Identity:
    """Who the website request is, as far as the governed path needs it."""

    def __init__(self, actor_id: str, scope: Any, org_ids: tuple[str, ...], user_id: Optional[str] = None) -> None:
        self.actor_id, self.scope, self.org_ids = actor_id, scope, org_ids
        self.user_id = str(user_id).lower() if user_id else None       # also the id of the person's own wallet account


async def resolve_identity(verifier: Any, pool: Any, request: Request, is_local: Callable[[Request], bool]
                           ) -> tuple[Optional[Identity], Optional[tuple[str, int]]]:
    """Website-session token -> Identity (with organization memberships, pilot enrolment applied), or (None, (why, status))."""
    from app.services import auth_context as ac
    from app.services.access import AccessScope

    cfg = getattr(verifier, "_oidc_config", None)
    if cfg is None:
        if is_local(request):                      # a bare local checkout: loopback only, the single local user
            return Identity("local-user", AccessScope.for_user("local-user"), ()), None
        return None, ("sign-in is not configured on this server", 503)
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not token:
        return None, ("sign in first", 401)
    from app.config import settings
    from app.services.authn import IdentityInactive, TokenRejected, supabase_token_allowed, validate_token_async

    try:
        actor = await validate_token_async(token, config=cfg, jwks_provider=verifier._jwks_provider)
    except TokenRejected:
        return None, ("your session is not valid; sign in again", 401)
    why = supabase_token_allowed(actor, cfg, surface="api", split=bool(getattr(settings, "split_supabase_tokens", True)))
    if why:
        return None, (why, 401)
    if pool is None:
        return None, ("the server is starting; retry shortly", 503)
    try:
        ctx = await ac.resolve_auth_context(pool, actor)
    except IdentityInactive:
        return None, ("this account is inactive", 403)
    except Exception:  # noqa: BLE001 -- cannot establish identity state: no access
        return None, ("could not verify your account right now", 503)
    if ac.EXECUTION_RUN not in ctx.scopes:
        return None, ("this account may not run models", 403)
    org_ids = tuple(ctx.org_ids)
    from app.services import pilot_enrol as pilot
    from app.services import accounts
    if accounts.enabled():
        org_ids = await accounts.resolve_orgs(pool, user_id=ctx.user_id, org_ids=org_ids)
    elif await pilot.ensure_pilot_member(pool, user_id=ctx.user_id, org_ids=org_ids):
        org_ids = (*org_ids, pilot.pilot_org_id())
    scope = (AccessScope.for_org_member(actor.subject, list(org_ids), ctx.user_id) if org_ids
             else AccessScope.for_user(actor.subject))
    return Identity(actor.subject, scope, org_ids, ctx.user_id), None


def paying_org(requested: Optional[str], org_ids: tuple[str, ...], pilot_org: Optional[str],
               own_account: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """(account, error). A named account must be one the person belongs to (their own wallet account counts); unnamed -> the
    pilot org when configured and they are in it; else a team they belong to when it is the only one, else their own wallet."""
    member = {o.lower() for o in org_ids}
    own = own_account.lower() if own_account else None
    if requested:
        req = requested.strip().lower()
        return (req, None) if req in member else (None, "you are not a member of that organization")
    if pilot_org and pilot_org in member:
        return pilot_org, None
    teams = member - ({own} if own else set())
    if len(teams) == 1:
        return next(iter(teams)), None
    if not teams and own in member:
        return own, None
    if len(member) == 1:
        return next(iter(member)), None
    return None, None


def register(server: Any, verifier: Any, pool_getter: Callable[[], Any], is_local: Callable[[Request], bool]) -> None:
    """Attach the routes to the MCP server's custom-route table."""

    async def authed(request: Request):
        if not BY_ADDRESS.allow(client_address(request)):
            return None, ("too many requests from this address; slow down", 429)
        who, refusal = await resolve_identity(verifier, pool_getter(), request, is_local)
        if who is not None and not BY_PERSON.allow(who.actor_id):
            return None, ("too many requests; wait a minute", 429)
        return who, refusal

    @server.custom_route("/v1/models", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def models(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        who, refusal = await authed(request)
        if refusal is not None:
            return _error(request, refusal[1], refusal[0], _kind(refusal[1]))
        from app.providers import chat as _chat
        return JSONResponse({"object": "list", "data": await _chat.list_models(who.scope)},
                            headers={**cors_headers(request), "Cache-Control": "no-store"})

    @server.custom_route("/v1/chat/completions", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def completions(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        who, refusal = await authed(request)
        if refusal is not None:
            return _error(request, refusal[1], refusal[0], _kind(refusal[1]))
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return _error(request, 413, "the request body is too large", "invalid_request_error", "request_too_large")
        try:
            raw = await request.json()
        except ValueError:
            return _error(request, 400, "the request body is not valid JSON", "invalid_request_error", "invalid_json")
        from app.config import settings
        from app.providers import chat as _chat
        from app.providers.types import ProviderCallDenied, ProviderCallFailed
        from app.services import pilot_enrol as pilot

        org, why = paying_org(request.headers.get("x-stealthlab-org"), who.org_ids, pilot.pilot_org_id(), who.user_id)
        if why:
            return _error(request, 403, why, "permission_error", "policy_denied")
        try:
            max_cost = float(request.headers["x-stealthlab-max-cost-usd"]) if request.headers.get("x-stealthlab-max-cost-usd") else None
        except ValueError:
            return _error(request, 400, "X-Stealthlab-Max-Cost-Usd must be a number", "invalid_request_error", "invalid_request")
        pool = pool_getter()
        try:
            params = _chat.parse_chat_body(raw)
            outcome = await _chat.chat(
                pool, who.scope, params, fallback_models=[], actor=who.actor_id,
                tenant_id=org or (who.org_ids[0] if who.org_ids else None), org_id=org,
                governed=settings.deployment_mode == "shared",
                data_class=request.headers.get("x-stealthlab-data-class") or "USER_PRIVATE",
                max_cost_usd=max_cost, tool=TOOL_NAME)
        except (_chat.ChatRequestInvalid, ProviderCallDenied, ProviderCallFailed) as exc:
            return _failure(request, exc)
        headers = {**cors_headers(request), "x-stealthlab-unit": outcome.unit, "x-stealthlab-connection": outcome.connection_id,
                   "x-stealthlab-ai-generated": "true"}
        if outcome.stream is not None:
            headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return StreamingResponse(outcome.stream, media_type="text/event-stream", headers=headers)
        if outcome.usage.get("cost_usd") is not None:
            headers["x-stealthlab-cost-usd"] = f"{outcome.usage['cost_usd']:.6f}"
        headers["Cache-Control"] = "no-store"
        return JSONResponse(outcome.json, headers=headers)
