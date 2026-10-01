"""
Reports and moderation for published ways.

submit_way puts a way live through an automated screen (economy/content_screen.py) with no human review. This
module is what stands behind that screen once a way is live:

  report_way      a signed-in user flags a way (malicious | nsfw | spam | broken | other). One report per user
                  per way. A malicious or NSFW report re-runs the content screen on the way itself: if the screen
                  now flags it, the way is hidden at once. Otherwise it is hidden when HIDE_AFTER_REPORTS
                  different users have reported it. A way that earned `verified` through evidenced reuse is never
                  auto-hidden by reports -- that needs an admin.
  withdraw_way    the person who submitted a way takes it down (their own submissions only).
  moderate_way    an admin hides, restores or removes a way.

"Hidden" is availability='quarantined' and "removed"/"withdrawn" is availability='disabled' -- the existing
procedure states every retrieval path already excludes (applicability._CANDIDATE_BASE_WHERE). Nothing is
deleted: the rows, the reports and an append-only event per action stay (way_moderation_events, plus the
procedure change-set audit). The change is written on the way's home shard and its search projection and its
Goal's has_procedures are re-queued on the control database, so search stops offering it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from app.utils.ids import uuid7

CATEGORIES = ("malicious", "nsfw", "spam", "broken", "other")
HIDE_AFTER_REPORTS = int(os.environ.get("KEL_HIDE_AFTER_REPORTS", "3"))
_SCREENED = ("malicious", "nsfw")
_STATE = {"hidden": "quarantined", "restored": "active", "removed": "disabled", "withdrawn": "disabled"}


class ModerationError(ValueError):
    pass


async def _set_availability(pool: Any, proc: dict, action: str, *, actor: str, reason: str) -> None:
    """Every live version of the way, on its home shard; then the audit and the projections."""
    from app.services import search_projection as sp
    from app.services.changeset_record import record_change_set, status_change
    from app.services.shards import home_pool

    stable, row_id = str(proc["procedure_id"]), str(proc["id"])
    state = _STATE[action]
    home = await home_pool(pool, "procedure", row_id, by_row_id=True)
    rows = await home.fetch(
        "UPDATE procedures SET availability = $2::procedure_availability, updated_at = now() "
        "WHERE procedure_id = $1::uuid AND t_invalid IS NULL RETURNING id::text", stable, state)
    await pool.execute(
        "INSERT INTO way_moderation_events (id, procedure_id, procedure_row_id, action, actor, reason) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6)", str(uuid7()), stable, row_id, action, actor, reason)
    await record_change_set(home, author=actor, reason=f"{action}: {reason}",
                            operations=[status_change(r["id"], {"availability": state}) for r in rows] or
                            [status_change(row_id, {"availability": state})])
    await sp.enqueue(pool, "procedure", stable)
    if proc.get("achieves_goal_id"):
        await sp.enqueue(pool, "goal", str(proc["achieves_goal_id"]))


def _way_text(proc: dict) -> dict:
    steps = proc.get("steps") or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    return {"name": proc.get("name"), "goal": proc.get("goal"), "steps": steps,
            "preconditions": proc.get("preconditions")}


async def report_way(pool: Any, *, proc: dict, reporter: str, category: str, detail: str = "",
                     screen=None) -> dict:
    """File a report; hide the way when the re-screen flags it or enough people report it. Never raises for a
    repeat report -- it says so."""
    if category not in CATEGORIES:
        raise ModerationError(f"category must be one of {list(CATEGORIES)}")
    if not reporter:
        raise ModerationError("a report needs a signed-in user")
    stable = str(proc["procedure_id"])

    verdict = None
    if category in _SCREENED:
        if screen is None:
            from app.economy.content_screen import screen_contribution as screen
        verdict = (await screen({**_way_text(proc), "reported_as": category})).as_dict()

    inserted = await pool.fetchval(
        "INSERT INTO way_reports (id, procedure_id, procedure_row_id, reporter, category, detail, screen_verdict) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::jsonb) "
        "ON CONFLICT (procedure_id, reporter) DO NOTHING RETURNING id::text",
        str(uuid7()), stable, str(proc["id"]), reporter, category, detail[:2000],
        json.dumps(verdict) if verdict else None)
    reporters = await pool.fetchval("SELECT count(DISTINCT reporter) FROM way_reports WHERE procedure_id = $1::uuid",
                                    stable)
    result: dict = {"procedure_id": stable, "reported": inserted is not None, "reports": int(reporters),
                    "hidden": False}
    if inserted is None:
        result["note"] = "you already reported this way"
        return result

    already_hidden = proc.get("availability") in ("quarantined", "disabled")
    verified = proc.get("verification_state") == "verified"
    flagged = bool(verdict) and not verdict.get("allowed", True) and "unscreened" not in (verdict.get("categories") or [])
    if already_hidden:
        result["hidden"] = True
    elif verified:
        result["note"] = "this way is verified by evidence; an admin reviews reports against it"
    elif flagged:
        await _set_availability(pool, proc, "hidden", actor="system:report-rescreen",
                                reason=f"reported as {category}; the content screen agreed: {verdict.get('reason')}")
        result.update(hidden=True, why="the content screen flagged it on re-check")
    elif reporters >= HIDE_AFTER_REPORTS:
        await _set_availability(pool, proc, "hidden", actor="system:reports",
                                reason=f"{reporters} people reported it (threshold {HIDE_AFTER_REPORTS})")
        result.update(hidden=True, why=f"{reporters} people reported it")
    return result


async def withdraw_way(pool: Any, *, submission_id: str, actor: str) -> dict:
    """The submitter takes their own way down. Only the account that submitted it can."""
    from app.services.procedures import get_procedure

    sub = await pool.fetchrow("SELECT id::text, submitted_by, procedure_row_id::text AS procedure_row_id, status "
                              "FROM procedure_submissions WHERE id = $1::uuid", submission_id)
    if sub is None or sub["submitted_by"] != actor:
        raise ModerationError("submission not found among yours")
    if not sub["procedure_row_id"]:
        raise ModerationError("that submission never produced a way")
    proc = await get_procedure(pool, sub["procedure_row_id"])
    if proc is None:
        raise ModerationError("the way is no longer there")
    await _set_availability(pool, proc, "withdrawn", actor=actor, reason="withdrawn by the person who submitted it")
    await pool.execute("UPDATE procedure_submissions SET status = 'rejected', "
                       "status_reason = 'withdrawn by the submitter' WHERE id = $1::uuid", submission_id)
    return {"submission_id": submission_id, "procedure_id": str(proc["procedure_id"]), "withdrawn": True}


async def moderate_way(pool: Any, *, procedure_row_id: str, action: str, actor: str, reason: str) -> dict:
    """Admin: hide | restore | remove."""
    from app.services.procedures import get_procedure

    mapped = {"hide": "hidden", "restore": "restored", "remove": "removed"}.get(action)
    if mapped is None:
        raise ModerationError("action must be hide, restore or remove")
    if not reason.strip():
        raise ModerationError("a reason is required")
    proc = await get_procedure(pool, procedure_row_id)
    if proc is None:
        raise ModerationError("way not found")
    await _set_availability(pool, proc, mapped, actor=actor, reason=reason.strip())
    return {"procedure_id": str(proc["procedure_id"]), "action": mapped, "availability": _STATE[mapped]}


async def reports_for(pool: Any, procedure_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT reporter, category, detail, screen_verdict, created_at FROM way_reports "
                            "WHERE procedure_id = $1::uuid ORDER BY created_at", procedure_id)
    return [dict(r) for r in rows]
