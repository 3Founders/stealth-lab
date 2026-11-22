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
_PUBLIC_STEP_FIELDS = ("order", "kind", "title", "description", "prompt", "locator", "status")


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
        "SELECT price_paise, price_currency FROM procedures WHERE id::text = $1 AND t_invalid IS NULL",
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
            "purchase_status": purchase, "licence_status": legacy}


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
        # Price and licence are about this caller and this exact version (docs/procedure_billing.md §7.1).
        # `price_paise` null/absent means free; a signed-out reader is always "none".
        **await _price_and_licence(pool, row, viewer_id),
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


async def cancel_run(pool: Any, owner_id: str, run_id: str) -> dict:
    from app.execution import way_runs

    return await way_runs.report(pool, run_id, owner_id=owner_id, cancel=True)


from app.services.procedure_billing import ProcedureNotPurchased  # the paid-Way gate's named refusal

ERRORS = (intake.SubmissionError, runs.RunError, LookupError, ProcedureNotPurchased)
