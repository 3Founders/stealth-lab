"""
HTTP side of contributions (``POST /v1/contributions``): a PERSON adding a tool, a script, a better prompt or a tip to a goal,
from the kell website. The agent side is the MCP tool ``submit`` (server.py); both call app/services/contributions.py, so a
suggestion is stored, screened, rate-limited and redacted the same way whoever makes it.

Who may call: a signed-in person, by their Supabase WEBSITE-SESSION token (``Gate`` from delegate_routes: it refuses MCP OAuth
tokens, and with no identity provider accepts only loopback as the single local user). CORS is the same explicit allow-list as
the other website routes, never ``*``, never with credentials.

What is stored is a PRIVATE candidate owned by the person, linked to the goal (same as report_discovery); nothing is published
or verified. `suggested_by` is always "user" here: a person typed it.

Body: {kind: tool|script|prompt|tip, title, content, why, goal_id | goal + goal_objective, procedure_id?, step_order?, repo?}
Answers: 201 {outcome:"stored", claim_id, goal, ...}; 200 {outcome:"goal_ambiguous", candidates} or {outcome:"rejected_by_screen",
screen} (nothing written); 400 {error} for a bad field; 429 {error} when rate limited; 401/403/503 as for the other routes.
"""
from __future__ import annotations

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.mcp_server.delegate_routes import Gate, allowed_origins
from app.services import contributions as ct

FIELDS = ("kind", "title", "content", "why", "goal_id", "goal", "goal_objective", "procedure_id", "step_order", "repo")


def cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    if origin and origin in allowed_origins():
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "authorization, content-type",
                "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Max-Age": "600"}
    return {"Vary": "Origin"}


def _json(request: Request, body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})


def _error(request: Request, message: str, status: int) -> JSONResponse:
    return _json(request, {"error": message}, status)


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any], *, find_or_create_goal: Callable[..., Any],
             resolve_procedure: Callable[..., Any]) -> None:
    """Attach the route to the MCP server's custom-route table. The two callables are the server's own (finding or creating a
    goal, resolving a procedure), passed in so this module does not import the server."""

    @server.custom_route("/v1/contributions", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def contributions(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return _error(request, "the request body must be JSON", 400)
        if not isinstance(data, dict):
            return _error(request, "the request body must be a JSON object", 400)
        step = data.get("step_order")
        if step is not None and (not isinstance(step, int) or isinstance(step, bool)):
            return _error(request, "step_order must be a whole number", 400)
        from app.services.access import AccessScope

        out = await ct.submit_contribution(
            pool, AccessScope(viewer_id=subject), find_or_create_goal=find_or_create_goal, resolve_procedure=resolve_procedure,
            created_by=f"website:{subject}", suggested_by="user",
            **{k: data.get(k) for k in FIELDS if k not in ("step_order",)}, step_order=step)
        if isinstance(out, str):
            message = out.removeprefix("REFUSED: ")
            return _error(request, message, 429 if message.startswith("rate limit") else 400)
        return _json(request, out, 201 if out.get("outcome") == "stored" else 200)
