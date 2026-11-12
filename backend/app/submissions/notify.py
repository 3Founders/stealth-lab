"""The email a submitter gets when a step of theirs is rejected: which step, and why.

Plain text through Resend (the contact form's provider). From is a fixed setting (SUBMISSION_NOTIFY_FROM, else
CONTACT_NOTIFY_FROM), never anything the submitter sent; the submitter's own text appears only in the body. The address
is the one on their kell account (users.email by their sign-in subject). Best effort: False, never an error, when email
is not configured or the account has no address; the steps' state in the database is the record either way.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

RESEND_URL = "https://api.resend.com/emails"
log = logging.getLogger(__name__)


async def owner_email(pool: Any, owner_id: str) -> Optional[str]:
    try:
        email = await pool.fetchval(
            "SELECT email FROM users WHERE (external_subject = $1 OR id::text = $1) AND email IS NOT NULL "
            "AND is_active ORDER BY t_created DESC LIMIT 1", owner_id)
    except Exception:  # noqa: BLE001 -- no identity table here: no address
        return None
    email = (email or "").strip()
    return email if "@" in email and "\n" not in email and "\r" not in email else None


def rejection_text(sub: dict, rejected: list[dict]) -> tuple[str, str]:
    """-> (subject, body)."""
    subject = "Your kell submission needs changes"
    lines = [f"Your submission \"{sub['goal_name']}\" was reviewed. "
             f"{len(rejected)} of its {len(sub['steps'])} steps could not be approved:", ""]
    for s in rejected:
        lines += [f"Step {s['order']}: {s['title']}", f"  Why: {s['reason'] or 'no reason recorded'}", ""]
    approved = [s for s in sub["steps"] if s["status"] in ("ai_approved", "approved")]
    if approved:
        lines += [f"The other {len(approved)} step(s) are approved and stay approved.", ""]
    lines += ["Fix those steps and resubmit them; nothing else needs to change.",
              f"Submission id: {sub['id']}", "", "-- kell"]
    return subject, "\n".join(lines)


async def send_rejection(pool: Any, sub: dict, *, client: Optional[httpx.AsyncClient] = None) -> bool:
    from app.config import settings

    rejected = [s for s in sub["steps"] if s["status"] == "rejected"]
    if not rejected:
        return False
    key = settings.resend_api_key
    sender = os.environ.get("SUBMISSION_NOTIFY_FROM") or settings.contact_notify_from
    to = await owner_email(pool, sub["owner_id"])
    if not (key and sender and to):
        log.info("submission %s: rejection email not sent (email %s, address %s)", sub["id"],
                 "configured" if key and sender else "not configured", "on file" if to else "missing")
        return False
    subject, body = rejection_text(sub, rejected)
    payload = {"from": sender, "to": [to], "subject": subject, "text": body}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if client is not None:
            r = await client.post(RESEND_URL, json=payload, headers=headers, timeout=10.0)
        else:
            async with httpx.AsyncClient() as c:
                r = await c.post(RESEND_URL, json=payload, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("submission %s: rejection email failed (%s)", sub["id"], type(exc).__name__)
        return False
    if not 200 <= r.status_code < 300:
        log.warning("submission %s: rejection email answered %s", sub["id"], r.status_code)
        return False
    await pool.execute("UPDATE goal_submissions SET notified_at = now() WHERE id = $1::uuid", sub["id"])
    return True
