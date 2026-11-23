"""Goal submission over HTTP (docs/goal_submission.md). Signed-in callers only; everyone sees and changes only their own.

    POST /v1/goals/submissions/preview          {goal, description, steps}  -> the mode it will be filed under
    POST /v1/goals/submissions/refine           {goal, description, steps, mode, instruction?}  -> the same Way rewritten
                                                (improve | specialize | abstract), shown and not stored
    POST /v1/goals/submissions                  {goal, description, steps}  -> stored and reviewed, step by step
    GET  /v1/goals/submissions                  -> the caller's submissions
    GET  /v1/goals/submissions/{id}             -> one, with each step's state and the reason for a rejection
    GET  /v1/goals/submissions/{id}/reviews     -> public: ratings and comments, newest first
    POST /v1/goals/submissions/{id}/reviews     {rating, body}  -> your review (edits your earlier one)
    POST /v1/goals/submissions/{id}/steps/{n}   {kind, description | prompt, locator}  -> fix step n, reviewed again
    POST /v1/goals/submissions/{id}/steps       {steps: [{order, ...}]}  -> fix several steps, reviewed together
    POST /v1/goals/submissions/{id}/rewrite     {instruction}  -> kell's rewrite of the steps, a proposal (nothing stored)
    POST /v1/goals/submissions/{id}/runs        {input}  -> run the approved Way; it pauses at a human step
    GET  /v1/goals/runs                         ?all=1  -> the caller's runs, newest first (open ones unless all=1)
    GET  /v1/goals/runs/{run_id}
    POST /v1/goals/runs/{run_id}/done           {note}  -> the person did the step the run waits on; it continues
    POST /v1/goals/runs/{run_id}/cancel         -> stop the run
    GET  /v1/goals/runs/{run_id}/artifacts/{name}  -> one file this run produced, as a download
    GET  /v1/goals/runs/{run_id}/artifacts.zip     -> all of them, as one archive

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

    @server.custom_route("/v1/goals/submissions/refine", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def submission_refine(request: Request) -> Response:
        # Before the `{submission_id}` route below, or "refine" would be read as an id.
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        mode = str(body.pop("mode", "") or "")
        instruction = str(body.pop("instruction", "") or "")
        return await _answer(request, api.refine(pool, subject, body, mode=mode, instruction=instruction))

    @server.custom_route("/v1/goals/submissions", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    async def submissions(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        if request.method == "GET":
            return _json(request, {"submissions": await api.mine(pool, subject)})
        return await _answer(request, api.submit(pool, subject, await _body(request)))

    @server.custom_route("/v1/goals/submissions/public", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def submissions_public(request: Request) -> Response:
        """The published-Ways catalogue. PUBLIC: the homepage and task search read it without an account.

        Registered BEFORE `/{submission_id}` or "public" would be read as an id. Only approved Ways;
        the owner, the reviewer and the internal goal ids are never in the projection.
        """
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        q = str(request.query_params.get("q") or "")[:200]
        try:
            limit = int(request.query_params.get("limit") or 12)
        except ValueError:
            limit = 12
        try:
            offset = int(request.query_params.get("offset") or 0)
        except ValueError:
            offset = 0
        limit = max(1, min(limit, 50))
        offset = max(0, min(offset, 10_000))
        # Fetch one extra row to answer `has_more` without a second count query; the extra row is
        # never returned. A client can then stop cleanly instead of asking for one empty page.
        rows = await api.list_public(pool, q=q, limit=limit + 1, offset=offset)
        ways = rows[:limit]
        return _json(request, {"ways": ways, "has_more": len(rows) > limit,
                               "next_offset": offset + len(ways)})

    @server.custom_route("/v1/goals/submissions/{submission_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def submission_get(request: Request) -> Response:
        # PUBLIC. A Way page is a sales page, so this endpoint is readable without an account (docs/procedure_outputs.md).
        # A token, when present, is an upgrade: the owner gets their full record, anyone else gets the public view.
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        viewer, refusal = await gate.optional_subject(request)
        if refusal is not None:
            return _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        submission_id = request.path_params["submission_id"]
        try:
            found = await api.get_public(pool, viewer, submission_id)
        except LookupError:
            found = None
        except ValueError:
            # A malformed id is a 404 to a reader, not a 500: there is no such Way, whoever asked.
            found = None
        if found is None:
            return _error(request, "no such Way", 404)
        return _json(request, found)

    @server.custom_route("/v1/goals/submissions/{submission_id}/reviews", methods=["GET", "POST", "OPTIONS"],
                         include_in_schema=False)
    async def submission_reviews(request: Request) -> Response:
        """Reviews on a Way. GET is public (a buyer reads them before paying); POST needs a person.

        Opinion lives here and measurement lives in the evidence panel: a rating never changes a
        success rate, and this endpoint never returns one.
        """
        from app.services import way_reviews

        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        submission_id = request.path_params["submission_id"]
        if request.method == "GET":
            try:
                limit = int(request.query_params.get("limit") or 50)
            except ValueError:
                limit = 50
            try:
                return _json(request, await way_reviews.list_for(pool, submission_id, limit=limit))
            except ValueError:
                return _error(request, "no such Way", 404)
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        try:
            review = await way_reviews.upsert(
                pool, submission_id=submission_id, author_user_id=subject,
                rating=body.get("rating"), body=body.get("body"),
                procedure_row_id=(str(body.get("procedure_row_id")) if body.get("procedure_row_id") else None),
                source="website")
        except LookupError:
            return _error(request, "no such Way", 404)
        except way_reviews.ReviewError as exc:
            return _error(request, str(exc), 400)
        return _json(request, {"review": review}, 201)

    @server.custom_route("/v1/goals/submissions/{submission_id}/steps/{order:int}", methods=["POST", "DELETE", "OPTIONS"],
                         include_in_schema=False)
    async def submission_fix_step(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        order = request.path_params["order"]
        if request.method == "DELETE":
            # A step taken out of a Way that is still being written (not only a failed one): the rest close the gap and
            # are reviewed again, because a Way that lost a step is not the Way that passed.
            return await _answer(request, api.remove_step(pool, subject, request.path_params["submission_id"], order))
        return await _answer(request, api.fix_step(pool, subject, request.path_params["submission_id"],
                                                   order, await _body(request)))

    @server.custom_route("/v1/goals/submissions/{submission_id}/steps", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def submission_fix_steps(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.fix_steps(pool, subject, request.path_params["submission_id"], body.get("steps")))

    @server.custom_route("/v1/goals/submissions/{submission_id}/rewrite", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def submission_rewrite(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.rewrite(pool, subject, request.path_params["submission_id"],
                                                  str(body.get("instruction") or "")))

    @server.custom_route("/v1/goals/submissions/{submission_id}/suggestions", methods=["GET", "OPTIONS"],
                         include_in_schema=False)
    async def submission_suggestions(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        # Searched when asked, never stored: a remembered suggestion goes stale when a Way is renamed or deleted
        # (owner's call, 2026-11-18), and a dead "try this instead" is worse than none.
        return await _answer(request, api.suggestions(pool, subject, request.path_params["submission_id"]))

    @server.custom_route("/v1/goals/submissions/{submission_id}/related", methods=["GET", "OPTIONS"],
                         include_in_schema=False)
    async def submission_related(request: Request) -> Response:
        """Related Ways for the rail, and the ones offered after the final output (both labelled honestly)."""
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.related_ways(pool, request.path_params["submission_id"]))

    @server.custom_route("/v1/goals/submissions/{submission_id}/steps/cost", methods=["GET", "OPTIONS"],
                         include_in_schema=False)
    async def submission_step_cost(request: Request) -> Response:
        """The price split per step, so a restart from step N is priced as steps N..end."""
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.step_costs(pool, request.path_params["submission_id"]))

    @server.custom_route("/v1/goals/submissions/{submission_id}/steps/{order:int}/inputs",
                         methods=["PUT", "OPTIONS"], include_in_schema=False)
    async def submission_step_inputs(request: Request) -> Response:
        """The author declares what a step asks the buyer for. Prompts stay immutable; inputs are the editable part."""
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.set_step_inputs(
            pool, subject, request.path_params["submission_id"], int(request.path_params["order"]),
            body.get("inputs")))

    @server.custom_route("/v1/goals/submissions/{submission_id}/runs", methods=["POST", "OPTIONS"],
                         include_in_schema=False)
    async def submission_run(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.start_run(pool, subject, request.path_params["submission_id"],
                                                    str(body.get("input") or "")))

    @server.custom_route("/v1/goals/runs", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def runs_mine(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        open_only = request.query_params.get("all") not in ("1", "true")
        return _json(request, {"runs": await api.my_runs(pool, subject, open_only=open_only)})

    @server.custom_route("/v1/goals/runs/{run_id}/cancel", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def run_cancel(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.cancel_run(pool, subject, request.path_params["run_id"]))

    @server.custom_route("/v1/goals/runs/{run_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def run_get(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await _answer(request, api.get_run(pool, subject, request.path_params["run_id"]))

    @server.custom_route("/v1/goals/runs/{run_id}/artifacts/{name}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def artifact_one(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await api.download_artifact(pool, subject, request.path_params["run_id"],
                                           request.path_params["name"], request)

    @server.custom_route("/v1/goals/runs/{run_id}/artifacts.zip", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def artifact_all(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        return await api.download_artifacts_zip(pool, subject, request.path_params["run_id"], request)

    @server.custom_route("/v1/goals/runs/{run_id}/done", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def run_done(request: Request) -> Response:
        subject, pool, early = await _begin(request)
        if early is not None:
            return early
        body = await _body(request)
        return await _answer(request, api.finish_step(pool, subject, request.path_params["run_id"],
                                                      str(body.get("note") or "")))
