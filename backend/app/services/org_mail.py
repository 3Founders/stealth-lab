"""The invitation email an organization admin can send.

It is best effort, like the submission and contact mail: when Resend is not configured, or the address is missing, the
invite still exists and the admin still gets the one-time code to share. The database row is the record; this module only
tries to save the admin a copy-and-paste.

Nothing here logs the code. The code is the credential, so it appears only in the message body sent to the invitee.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from app.config import settings

RESEND_URL = "https://api.resend.com/emails"
log = logging.getLogger(__name__)


def invite_text(*, org_name: str, code: str, role: str, expires_at: str, link: str) -> tuple[str, str]:
    """-> (subject, body). Plain text; the link carries the code, and the code is also printed for a manual paste."""
    subject = f"You're invited to {org_name} on kell"
    body = "\n".join([
        f"You have been invited to join {org_name} on kell as {role}.",
        "",
        "Open this link and sign in:",
        link,
        "",
        "Or paste this invite code at the join page:",
        code,
        "",
        f"The invite is valid until {expires_at}.",
        "",
        "If you were not expecting this, you can ignore the message.",
        "",
        "-- kell",
    ])
    return subject, body


async def send_invite(*, to: str, org_name: str, code: str, role: str, expires_at: str, link: str,
                      client: Optional[httpx.AsyncClient] = None) -> bool:
    """Send one invitation. False (not an error) when email is not configured or the provider refuses it."""
    key = settings.resend_api_key
    sender = os.environ.get("ORG_INVITE_FROM") or settings.contact_notify_from
    address = (to or "").strip()
    if not (key and sender and address and "@" in address):
        log.info("invite email not sent (email %s, address %s)",
                 "configured" if key and sender else "not configured", "on file" if address else "missing")
        return False
    subject, body = invite_text(org_name=org_name, code=code, role=role, expires_at=expires_at, link=link)
    payload = {"from": sender, "to": [address], "subject": subject, "text": body}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if client is not None:
            response = await client.post(RESEND_URL, json=payload, headers=headers, timeout=10.0)
        else:
            async with httpx.AsyncClient() as http:
                response = await http.post(RESEND_URL, json=payload, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("invite email failed (%s)", type(exc).__name__)
        return False
    if not 200 <= response.status_code < 300:
        log.warning("invite email answered %s", response.status_code)
        return False
    return True
