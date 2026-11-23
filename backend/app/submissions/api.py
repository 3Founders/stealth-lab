"""The calls every surface makes (HTTP routes in mcp_server/submission_routes.py, the MCP tools in server.py, the CLI),
so they cannot drift. Each returns plain JSON; a SubmissionError / RunError / LookupError message is what the person
reads.

Review runs right after a submit or a fix. It is awaited up to `wait` seconds; past that it keeps running in the
background and the answer says the steps are still `reviewing` (ask for the submission again).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.submissions import intake, public_catalog, review, runs, store

log = logging.getLogger(__name__)
REVIEW_WAIT = 90.0
_BACKGROUND: set[asyncio.Task] = set()


def preview(payload: Any) -> dict:
    return intake.preview(intake.parse(payload))


async def _refine_limit(pool: Any, owner_id: str) -> None:
    """A rewrite is one model call, so it has its own bucket: it stores nothing and runs no reviewer, so it must not
    spend the submission quota the person needs for the Way itself."""
    from datetime import timedelta

    from app.economy import constants as econ
    from app.services.governance import RateLimit, RateLimiter, RateLimitExceeded

    path = "goals/submissions/refine"
    limiter = RateLimiter(pool, limits={
        path: RateLimit(max_requests=econ.SUBMISSION_REFINE_RATE_LIMIT_MAX, window=timedelta(hours=1)),
    })
    try:
        await limiter.check_and_record(f"user:{owner_id}", path)
    except RateLimitExceeded as exc:
        raise intake.SubmissionError(f"too many rewrites; retry in {exc.retry_after_seconds}s") from exc


async def refine(pool: Any, owner_id: str, payload: Any, *, mode: str, instruction: str = "") -> dict:
    """Improve, specialize or abstract a draft (app/submissions/refine.py). Nothing is stored: the answer is shown and
    the person submits it through `submit`, so the normal review and limits apply to what they send."""
    from app.submissions import refine as refine_mod

    await _refine_limit(pool, owner_id)
    return await refine_mod.refine(payload, mode=mode, instruction=instruction)


async def _reviewed(pool: Any, submission_id: str, owner_id: str, wait: float) -> dict:
    task = asyncio.create_task(review.review_submission(pool, submission_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=wait)
    except asyncio.TimeoutError:
        pass
    except Exception:  # noqa: BLE001 -- the submission is stored; review_pending() retries it
        log.exception("submission %s: review failed", submission_id)
    return await store.get(pool, submission_id, owner_id=owner_id)


async def _limit(pool: Any, owner_id: str) -> None:
    """Each submit or fix costs a reviewer call: the submit_way limits (per person per hour and per day, and one bucket for
    everyone). Checked before anything is written."""
    from datetime import timedelta

    from app.economy import constants as econ
    from app.services.governance import RateLimit, RateLimiter, RateLimitExceeded

    path = "goals/submissions"
    limiter = RateLimiter(pool, limits={
        path: RateLimit(max_requests=econ.SUBMISSION_RATE_LIMIT_MAX,
                        window=timedelta(hours=econ.SUBMISSION_RATE_LIMIT_WINDOW_HOURS)),
        path + "/day": RateLimit(max_requests=econ.SUBMISSION_DAILY_MAX, window=timedelta(days=1)),
        path + "/all": RateLimit(max_requests=econ.SUBMISSION_GLOBAL_HOURLY_MAX, window=timedelta(hours=1)),
    })
    try:
        await limiter.check_and_record(f"user:{owner_id}", path)
        await limiter.check_and_record(f"user:{owner_id}", path + "/day")
        await limiter.check_and_record("global:goal_submissions", path + "/all")
    except RateLimitExceeded as exc:
        raise intake.SubmissionError(f"too many submissions; retry in {exc.retry_after_seconds}s") from exc


async def submit(pool: Any, owner_id: str, payload: Any, *, wait: float = REVIEW_WAIT) -> dict:
    sub = intake.parse(payload)
    await _limit(pool, owner_id)
    stored = await store.submit(pool, owner_id, sub)
    out = await _reviewed(pool, stored["submission_id"], owner_id, wait)
    out["message"] = intake.preview(sub)["message"].replace("will be", "was")
    return out


async def fix_step(pool: Any, owner_id: str, submission_id: str, order: int, step: Any, *,
                   wait: float = REVIEW_WAIT) -> dict:
    parsed = intake.parse({"goal": "-", "steps": [step]}).steps[0]
    await _limit(pool, owner_id)
    await store.replace_step(pool, submission_id, owner_id, int(order), parsed)
    return await _reviewed(pool, submission_id, owner_id, wait)


async def fix_steps(pool: Any, owner_id: str, submission_id: str, steps: Any, *, wait: float = REVIEW_WAIT) -> dict:
    """Replace several steps at once ([{order, kind, title, description | prompt, locator}]) and review them together: what
    an AI rewrite (rewrite below) sends back once the person has read it. One limit check and one review for the lot."""
    if not isinstance(steps, list) or not steps:
        raise intake.SubmissionError("send the steps to change: [{order, kind, title, description | prompt, locator}]")
    parsed = []
    for raw in steps[: intake.MAX_STEPS]:
        if not isinstance(raw, dict):
            raise intake.SubmissionError("each step is an object with its order")
        try:
            order = int(raw.get("order"))
        except (TypeError, ValueError) as exc:
            raise intake.SubmissionError("each step needs its order (1, 2, ...)") from exc
        step = {k: v for k, v in raw.items() if k != "order"}
        try:
            parsed.append((order, intake.parse({"goal": "-", "steps": [step]}).steps[0]))
        except intake.SubmissionError as exc:
            raise intake.SubmissionError(f"step {order}: {exc}") from exc
    if await store.get(pool, submission_id, owner_id=owner_id) is None:
        raise LookupError("no submission of yours with that id")
    await _limit(pool, owner_id)
    for order, step in parsed:
        await store.replace_step(pool, submission_id, owner_id, order, step)
    return await _reviewed(pool, submission_id, owner_id, wait)


async def rewrite(pool: Any, owner_id: str, submission_id: str, instruction: str = "") -> dict:
    """kell rewrites the submission's steps from one sentence and the reviewer's reasons (submissions/rewrite.py). Nothing
    is stored: the answer is a proposal for the form, sent with fix_steps once the person agrees."""
    from app.submissions import rewrite as _rewrite

    sub = await store.get(pool, submission_id, owner_id=owner_id)
    if sub is None:
        raise LookupError("no submission of yours with that id")
    await _limit(pool, owner_id)
    return await _rewrite.propose(sub, instruction)


async def remove_step(pool: Any, owner_id: str, submission_id: str, order: int, *, wait: float = REVIEW_WAIT) -> dict:
    """Take a step out of a submission the person owns. Not rate-limited: removing a step costs no reviewer call of its
    own (the steps that remain are re-reviewed because the Way changed, which is the same review they would get anyway)."""
    await store.remove_step(pool, submission_id, owner_id, int(order))
    return await _reviewed(pool, submission_id, owner_id, wait)


async def suggestions(pool: Any, owner_id: str, submission_id: str) -> dict:
    """Better Ways for each rejected step, searched now rather than remembered (review.suggest_for_submission)."""
    from app.submissions import review

    return await review.suggest_for_submission(pool, submission_id, owner_id=owner_id)


async def get(pool: Any, owner_id: str, submission_id: str) -> dict:
    out = await store.get(pool, submission_id, owner_id=owner_id)
    if out is None:
        raise LookupError("no submission of yours with that id")
    return out


# Fields a stranger may see. Everything else in the row is either internal (ids that leak volume), the owner's
# identity, or review machinery that only makes sense to the person fixing it.
_PUBLIC_STEP_FIELDS = ("order", "kind", "title", "description", "prompt", "locator", "status",
                       "produces", "imports")


async def _price_and_licence(pool: Any, row: dict, viewer_id: Optional[str]) -> dict:
    """The version's price and THIS caller's licence (docs/procedure_billing.md §7.1).

    `price_paise` null/absent means free; `licence_status` is about the caller, so a signed-out
    reader is always "none". The price is read from the exact parent procedure row, never from
    the submission, so a later version cannot inherit an older price.
    """
    from app.services import procedure_billing

    parent = row.get("parent_procedure_id")
    if not parent or pool is None or not hasattr(pool, "fetchrow"):
        # The projection tests drive get_public with no database; a page with no pool is a page with no price.
        return {"price_paise": None, "price_currency": "INR",
                "purchase_status": "none", "licence_status": "none"}
    proc = await pool.fetchrow(
        "SELECT price_paise, price_currency, parameter_schema FROM procedures WHERE id::text = $1 AND t_invalid IS NULL",
        str(parent))
    if proc is None:
        return {"price_paise": None, "price_currency": "INR", "licence_status": "none"}
    price = proc["price_paise"]
    currency = proc["price_currency"] or "INR"
    purchase = "none"
    if price is not None and int(price) > 0 and viewer_id:
        purchase = await procedure_billing.purchase_status(
            pool, procedure_row_id=parent, buyer_user_id=viewer_id)
    # The old field stays for clients that have not moved yet; "used" means the output was already
    # produced, so a new output needs a new purchase and the old field reads as "none".
    legacy = "purchased" if purchase == "paid" else ("refunded" if purchase == "refunded" else "none")
    return {"price_paise": price, "price_currency": currency,
            "purchase_status": purchase, "licence_status": legacy,
            # Declared inputs live on the procedure version, beside its price. Absent/empty = the Way takes nothing.
            "inputs": (proc["parameter_schema"] or {}).get("inputs", [])
            if isinstance(proc["parameter_schema"], dict) else []}


def _estimate_for(row: dict) -> dict:
    """The pre-run cost range for a submission's steps. Never raises: an estimate that breaks a page is worse than
    no estimate, and the page already renders an honest absence."""
    try:
        from app.submissions.cost_estimate import estimate_cost
        steps = [s for s in (row.get("steps") or []) if isinstance(s, dict)]
        if not steps:
            return {}
        return estimate_cost(steps).as_dict()
    except Exception:  # noqa: BLE001 -- an estimate must never take the page down
        return {}


async def get_public(pool: Any, viewer_id: Optional[str], submission_id: str) -> Optional[dict]:
    """The Way as a buyer sees it (docs/procedure_outputs.md, docs/procedure_billing.md).

    A Way page is a sales page: it has to be readable by someone with no account, or nobody can decide to buy.
    So this returns a projection for a stranger — the goal, the description, the steps, the status — and NEVER
    the owner's id, the internal goal/procedure ids, or a rejected step's reason (which is feedback for the
    author, not information for the public).

    The owner still gets the full record: pass their subject as `viewer_id` and an owned submission is returned
    unprojected, so the author's own view is unchanged.
    """
    if viewer_id:
        owned = await store.get(pool, submission_id, owner_id=viewer_id)
        if owned is not None:
            owned = dict(owned)
            owned.update(await _price_and_licence(pool, owned, viewer_id))
            return owned

    row = await store.get(pool, submission_id, owner_id=None)
    if row is None:
        return None
    # Only a published Way is public. A draft, a withdrawn one, or one still in review is the author's business.
    if row.get("status") not in ("approved", "ai_approved"):
        return None

    # Price, licence and declared inputs are all about this exact procedure version (docs/procedure_billing.md
    # §7.1). `price_paise` null/absent means free; a signed-out reader is always "none"; no inputs = it takes nothing.
    billing = await _price_and_licence(pool, row, viewer_id)

    public = {
        "id": row["id"],
        "goal_name": row.get("goal_name"),
        "description": row.get("description") or "",
        "mode": row.get("mode"),
        "mode_label": row.get("mode_label"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "parent_procedure_id": row.get("parent_procedure_id"),
        "steps": [
            {k: s.get(k) for k in _PUBLIC_STEP_FIELDS if k in s}
            for s in (row.get("steps") or [])
        ],
        **billing,
        "inputs": billing.get("inputs", []),
        # What this Way will probably cost to run, before anyone has run it: a RANGE, with its assumptions
        # (docs/pricingestimation.md §4). Absent once a run has measured the real figure.
        "cost_estimate": _estimate_for(row),
    }
    return public


async def mine(pool: Any, owner_id: str) -> list[dict]:
    return await store.list_for_owner(pool, owner_id)


async def list_public(pool: Any, *, q: str = "", limit: int = 12, offset: int = 0) -> list[dict]:
    """The published-Ways catalogue: the same projection as one Way page, without per-caller licence state."""
    rows = await public_catalog.list_public(pool, q=q, limit=limit, offset=offset)
    out: list[dict] = []
    for row in rows:
        row = dict(row)
        row["mode_label"] = intake.MODE_LABEL.get(row.get("mode"), row.get("mode") or "")
        row["price_currency"] = row.get("price_currency") or "INR"
        row["step_count"] = int(row.get("steps") or 0)
        out.append(row)
    return out


async def start_run(pool: Any, owner_id: str, submission_id: str, input: str = "", mode: str = "headless",
                    finder: Any = None) -> dict:
    """Run a submission's approved Way on the Way executor (app/execution/way_runs.py). "headless" (the website: no
    session to hand steps to) has kell do every automated step; the MCP tool runs it in "session" mode."""
    from app.execution import way_runs

    row = await pool.fetchrow("SELECT status, parent_procedure_id::text AS parent FROM goal_submissions "
                              "WHERE id::text = $1", submission_id)
    if row is None:
        raise runs.RunError("no submission with that id")
    if row["status"] not in ("ai_approved", "approved") or not row["parent"]:
        raise runs.RunError(f"this Way is {row['status'].replace('_', ' ')}; only an approved Way runs")
    return await way_runs.start(pool, row["parent"], owner_id=owner_id, input=input, mode=mode, finder=finder)


async def finish_step(pool: Any, owner_id: str, run_id: str, note: str = "") -> dict:
    """The person did what the run waits on. A run started before the Way executor continues on the older runner."""
    from app.execution import way_runs

    try:
        return await way_runs.report(pool, run_id, owner_id=owner_id, done=True, note=note)
    except LookupError:
        return await runs.finish_human_step(pool, run_id, owner_id=owner_id, note=note)


async def get_run(pool: Any, owner_id: str, run_id: str) -> dict:
    from app.execution import way_runs

    try:
        return await way_runs.get(pool, run_id, owner_id=owner_id)
    except LookupError:
        return await runs.get(pool, run_id, owner_id=owner_id)


async def my_runs(pool: Any, owner_id: str, *, open_only: bool = True) -> list[dict]:
    from app.execution import way_runs

    return await way_runs.mine(pool, owner_id, open_only=open_only)


async def related_ways(pool: Any, submission_id: str, *, limit: int = 6) -> dict[str, list[dict]]:
    """Ways related to this one, in two buckets the page labels separately.

    - **follows**: Ways that claim to apply after this one. There is no succession edge in the schema yet, so this
      is the honest approximation — another Way toward the same goal, or one published by the same author, that is
      not this Way. It is labelled "often done next", never "the next step", because kell cannot yet know.
    - **similar**: other published Ways toward the same goal.

    Both are discovery only. Nothing here is ranked by popularity: the ordering is the goal match, then recency.
    """
    row = await pool.fetchrow(
        """SELECT g.id::text AS id, g.goal_id::text AS goal_id, g.owner_id,
                  g.parent_procedure_id::text AS parent
           FROM goal_submissions g WHERE g.id::text = $1""", str(submission_id))
    if row is None:
        return {"follows": [], "similar": []}

    async def fetch(sql: str, *args) -> list[dict]:
        try:
            rows = await pool.fetch(sql, *args)
        except Exception:  # noqa: BLE001 -- a rail that cannot read must not take the page down
            return []
        return [{"id": r["id"], "goal_name": r["goal_name"], "price_paise": r["price_paise"],
                 "parent_procedure_id": r["parent"]} for r in rows]

    similar = await fetch(
        """SELECT g.id::text AS id, g.goal_name, p.price_paise, g.parent_procedure_id::text AS parent
           FROM goal_submissions g
           LEFT JOIN procedures p ON p.id = g.parent_procedure_id AND p.t_invalid IS NULL
           WHERE g.status IN ('approved', 'ai_approved') AND g.id <> $1::uuid
             AND g.goal_id IS NOT DISTINCT FROM $2::uuid
           ORDER BY g.created_at DESC LIMIT $3""",
        str(submission_id), row["goal_id"], int(limit))

    follows = []
    if row["owner_id"]:
        follows = await fetch(
            """SELECT g.id::text AS id, g.goal_name, p.price_paise, g.parent_procedure_id::text AS parent
               FROM goal_submissions g
               LEFT JOIN procedures p ON p.id = g.parent_procedure_id AND p.t_invalid IS NULL
               WHERE g.status IN ('approved', 'ai_approved') AND g.id <> $1::uuid AND g.owner_id = $2
               ORDER BY g.created_at DESC LIMIT $3""",
            str(submission_id), row["owner_id"], int(limit))

    seen = {r["id"] for r in similar}
    return {"follows": [f for f in follows if f["id"] not in seen], "similar": similar}


async def step_costs(pool: Any, submission_id: str) -> dict:
    """The Way's price split by step, so the page can show where the money goes and what a restart costs.

    A restart from step N re-runs only steps N..end, so the per-step split is what makes that restart priceable
    at all (`cost_estimate.per_step_prices` / `suffix_from`).
    """
    row = await pool.fetchrow(
        "SELECT steps FROM goal_submissions WHERE id::text = $1", str(submission_id))
    if row is None:
        raise intake.SubmissionError("no such Way", 404)
    steps = row["steps"]
    if isinstance(steps, str):
        import json as _json
        steps = _json.loads(steps)
    from app.submissions.cost_estimate import per_step_prices

    prices = per_step_prices([s for s in (steps or []) if isinstance(s, dict)])
    return {"steps": prices,
            "total_price_low_paise": round(sum(p["price_low_usd"] for p in prices) * 8400),
            "total_price_high_paise": round(sum(p["price_high_usd"] for p in prices) * 8400)}


async def set_step_inputs(pool: Any, owner_id: str, submission_id: str, order: int, inputs: Any) -> dict:
    """The author declares the inputs a step asks the buyer for.

    Prompts are frozen for the buyer (a run cannot change them), but inputs are exactly the part a person is meant to
    supply — so this is the author's editable surface, and the step's own `prompt` is untouched.
    """
    if inputs is not None and not isinstance(inputs, list):
        raise intake.SubmissionError("inputs must be a list (or null to declare none)")
    clean = []
    for item in inputs or []:
        if not isinstance(item, dict):
            raise intake.SubmissionError("each input must be an object")
        key = str(item.get("key") or "").strip()
        if not key or not re.match(r"^[a-zA-Z0-9_.-]{1,64}$", key):
            raise intake.SubmissionError("each input needs a key of letters, digits, dot, dash or underscore")
        clean.append({"key": key, "label": str(item.get("label") or key)[:120],
                      "required": bool(item.get("required", True)), "help": str(item.get("help") or "")[:300]})
    owned = await pool.fetchval(
        "SELECT 1 FROM goal_submissions WHERE id::text = $1 AND owner_id = $2", str(submission_id), str(owner_id))
    if not owned:
        raise intake.SubmissionError("no such Way of yours", 404)
    import json as _json

    done = await pool.fetchval(
        """UPDATE goal_submission_steps SET inputs = $3::jsonb, updated_at = now()
           WHERE submission_id = $1::uuid AND step_order = $2 RETURNING 1""",
        str(submission_id), int(order), _json.dumps(clean) if clean else None)
    if not done:
        raise intake.SubmissionError(f"this Way has no step {order}", 404)
    return {"order": int(order), "inputs": clean}


async def cancel_run(pool: Any, owner_id: str, run_id: str) -> dict:
    from app.execution import way_runs

    return await way_runs.report(pool, run_id, owner_id=owner_id, cancel=True)


# ------------------------------------------------------------------------- downloading what a run produced

async def _run_artifacts(pool: Any, run_id: str, owner_id: str) -> tuple[dict, list[dict]]:
    """(the run, the files it produced), after checking the caller may read them.

    Two checks, in this order and both refusal-first: the run must be the caller's own, and its Way must be one they
    hold a licence for (a free Way passes). Only then are keys read out of the run — a stranger's run id must not
    reveal even a filename.
    """
    from app.services import procedure_billing

    row = await pool.fetchrow(
        "SELECT submission_id::text AS submission_id, owner_id, outputs FROM way_runs WHERE id = $1::uuid",
        str(run_id))
    if row is None:
        raise LookupError("no run with that id")
    if str(row["owner_id"] or "") != str(owner_id or "") or not procedure_billing._uuid(owner_id):
        raise LookupError("no run with that id")          # the same answer as a missing run: no probing
    way_row = await _way_price_row(pool, row["submission_id"])
    if not await procedure_billing.may_download(pool, procedure_row_id=way_row,
                                                buyer_user_id=owner_id, run_id=str(run_id)):
        raise ProcedureNotPurchased(0, "INR")
    return {"submission_id": row["submission_id"]}, _stored_artifacts(row["outputs"])


async def _way_price_row(pool: Any, submission_id: str) -> str:
    """The procedures row id a submission runs, which is what a licence is bought against."""
    found = await pool.fetchval(
        "SELECT parent_procedure_id::text FROM goal_submissions WHERE id = $1::uuid", str(submission_id))
    return str(found or submission_id)


async def _artifact_bytes(artifact: dict) -> bytes:
    from app.services.object_storage import get_store

    store = get_store()
    if store is None:
        raise LookupError("the files for this run are not in storage")
    return await store.get(artifact["object_key"])


async def download_artifact(pool: Any, owner_id: str, run_id: str, name: str, request: Any) -> Any:
    """One file, forced as a download (never rendered on kell's origin)."""
    from starlette.responses import Response

    from app.mcp_server.room_routes import cors_headers
    from app.submissions import artifacts as art

    try:
        _, files = await _run_artifacts(pool, run_id)
    except LookupError as exc:
        return Response(str(exc), status_code=404, headers=cors_headers(request))
    except ProcedureNotPurchased:
        return Response("buy this Way before downloading its output", status_code=402,
                        headers=cors_headers(request))
    wanted = art.safe_name(name)
    match = next((a for a in files if a.get("name") == wanted), None)
    if match is None:
        return Response("this run produced no file with that name", status_code=404, headers=cors_headers(request))
    data = await _artifact_bytes(match)
    return Response(data, media_type=match.get("media_type") or "application/octet-stream", headers={
        **cors_headers(request), "Content-Disposition": art.content_disposition(wanted),
        "Cache-Control": "private, max-age=0, must-revalidate",   # a paid file is never cached publicly
    })


async def download_artifacts_zip(pool: Any, owner_id: str, run_id: str, request: Any) -> Any:
    """Every file the run produced, as one archive — what makes a multi-file Way feel like a finished thing."""
    from starlette.responses import Response

    from app.mcp_server.room_routes import cors_headers
    from app.submissions import artifacts as art

    try:
        run, files = await _run_artifacts(pool, run_id)
    except LookupError as exc:
        return Response(str(exc), status_code=404, headers=cors_headers(request))
    except ProcedureNotPurchased:
        return Response("buy this Way before downloading its output", status_code=402,
                        headers=cors_headers(request))
    if not files:
        return Response("this run produced no files", status_code=404, headers=cors_headers(request))
    got: list[tuple[str, bytes]] = []
    for a in files:
        got.append((a.get("name") or "output.txt", await _artifact_bytes(a)))
    name = art.safe_name(f"{run['submission_id'][:8]}-output.zip")
    return Response(art.zip_artifacts(got), media_type="application/zip", headers={
        **cors_headers(request), "Content-Disposition": art.content_disposition(name),
        "Cache-Control": "private, max-age=0, must-revalidate",
    })


from app.services.procedure_billing import ProcedureNotPurchased  # the paid-Way gate's named refusal

ERRORS = (intake.SubmissionError, runs.RunError, LookupError, ProcedureNotPurchased)
