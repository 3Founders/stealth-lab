"""Goal submission over HTTP (docs/goal_submission.md). Signed-in callers only; everyone sees and changes only their own.

    POST /v1/goals/submissions/preview          {goal, description, steps}  -> the mode it will be filed under
    POST /v1/goals/submissions                  {goal, description, steps}  -> stored and reviewed, step by step
    GET  /v1/goals/submissions                  -> the caller's submissions
    GET  /v1/goals/submissions/{id}             -> one, with each step's state and the reason for a rejection
    POST /v1/goals/submissions/{id}/steps/{n}   {kind, description | prompt, locator}  -> fix step n, reviewed again
    POST /v1/goals/submissions/{id}/runs        {input}  -> run the approved Way; it pauses at a human step
    GET  /v1/goals/runs/{run_id}
    POST /v1/goals/runs/{run_id}/done           {note}  -> the person did the step the run waits on; it continues

All the work is in app/submissions/api.py, shared with the MCP tools and the CLI.
"""
from __future__ import annotations

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.room_routes import _error, _json, cors_headers


def register(server: Any, gate: Any, pool_getter: Callable[[], Any]) -> None:
    from app.submissions import api

    async def _begin(request: Request) -> tuple[Any, Any, Any]:
        """-> (subject, pool, None) or (None, None, error response)."""
        if request.method == "OPTIONS":
            return None, None, Response(status_code=204, headers=cors_headers(request))
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return None, None, _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return None, None, _error(request, "the server is starting; retry shortly", 503)
        return subject, pool, None

    async def _body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    async def _answer(request: Request, call: Any) -> Response:
        try:
            return _json(request, await call)
        except api.ERRORS as exc:
            status = 404 if isinstance(exc, LookupError) else 400
            return _error(request, str(exc).strip("'\""), status)

    @server.custom_route("/v1/goals/submissions/preview", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def submission_preview(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        try:
            return _json(request, api.preview(await _body(request)))
        except api.ERRORS as exc:
            return _error(request, str(exc), 400)

    @server.custom_route("/v1/goals/submissions", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    async def submissions(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        if request.method == "GET":
            return _json(request, {"submissions": await api.mine(pool, subject)})
        return await _answer(request, api.submit(pool, subject, await _body(request)))

    @server.custom_route("/v1/goals/submissions/{submission_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def submission_get(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.get(pool, subject, request.path_params["submission_id"]))

    @server.custom_route("/v1/goals/submissions/{submission_id}/steps/{order:int}", methods=["POST", "OPTIONS"],
                         include_in_schema=False)
    async def submission_fix_step(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.fix_step(pool, subject, request.path_params["submission_id"],
                                                   request.path_params["order"], await _body(request)))

    @server.custom_route("/v1/goals/submissions/{submission_id}/runs", methods=["POST", "OPTIONS"],
                         include_in_schema=False)
    async def submission_run(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.start_run(pool, subject, request.path_params["submission_id"],
                                                    str(body.get("input") or "")))

    @server.custom_route("/v1/goals/runs/{run_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def run_get(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.get_run(pool, subject, request.path_params["run_id"]))

    @server.custom_route("/v1/goals/runs/{run_id}/done", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def run_done(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.finish_step(pool, subject, request.path_params["run_id"],
                                                      str(body.get("note") or "")))
