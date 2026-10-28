"""
The website's Contact form: validate, check Turnstile on the server, store, then tell the team.

Order matters and each step fails closed:
  1. a filled honeypot is dropped silently (a bot is not told it was caught);
  2. the rate limits (per address, per email, overall);
  3. Cloudflare Turnstile, verified HERE with the secret (the widget alone protects nothing). Tokens are single use and
     last 300 s. Any error, timeout or odd answer counts as a failure, never as a pass;
  4. the row is stored: it is the source of truth;
  5. the notification is best-effort. A failure leaves ``notified_at`` NULL and is logged without the visitor's text.

Email safety: the visitor's text never reaches a header. From and To are fixed settings; the visitor's address is only in
Reply-To (after a shape check that rejects CR/LF); the subject is a constant; the body is plain text; there is no auto-reply.

Honest scope: the rate limiter is in this process's memory (it resets on restart and is per worker), and it keys on the direct
socket address, which behind a proxy is the proxy (see app/api/deps.py scope_key_for). Turnstile is the main abuse control; the
limiter only stops a single client from hammering one worker. Retention (delete_after) is recorded but not enforced here.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.utils.ids import uuid7

log = logging.getLogger(__name__)

TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
RESEND_URL = "https://api.resend.com/emails"
TOPICS = ("general", "pilot", "security", "other")

_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class ContactRefused(Exception):
    """A request that must not proceed. ``status`` is the HTTP status; ``reason`` is safe to show the visitor."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def clean_line(value: Optional[str], limit: int) -> str:
    """One line of text: control characters (CR, LF, tabs included) become spaces, runs of space collapse, then cut."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value or "")).strip()[:limit]


def valid_email(value: str) -> bool:
    return 3 <= len(value) <= 254 and bool(_EMAIL.match(value))


class RateLimiter:
    """Sliding windows in memory: at most ``limit`` hits per key in ``window_s``. Nothing is stored beyond the timestamps."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


_by_address = RateLimiter(5, 3600)
_by_email = RateLimiter(3, 3600)
_overall = RateLimiter(300, 86400)


def reset_limits() -> None:  # tests
    for lim in (_by_address, _by_email, _overall):
        lim._hits.clear()


def _allowed_hostnames() -> set[str]:
    return {urlparse(o).hostname or "" for o in settings.frontend_origins} - {""}


async def verify_turnstile(token: str, remote_ip: Optional[str], *, client: Optional[httpx.AsyncClient] = None) -> bool:
    """True only when Cloudflare says success AND the challenge was solved on one of our own hostnames."""
    secret = settings.contact_turnstile_secret
    if not secret or not token or len(token) > 2048:
        return False
    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        if client is not None:
            r = await client.post(TURNSTILE_URL, data=data, timeout=5.0)
        else:
            async with httpx.AsyncClient() as c:
                r = await c.post(TURNSTILE_URL, data=data, timeout=5.0)
        body = r.json()
    except Exception:  # timeout, DNS, bad JSON: all are a failure, never a pass
        log.warning("turnstile verification could not be completed")
        return False
    if r.status_code != 200 or body.get("success") is not True:
        return False
    allowed = _allowed_hostnames()
    return not allowed or body.get("hostname") in allowed  # a token minted on someone else's site is refused


def check_ready() -> None:
    """Refuse when the form has nothing to verify with, instead of quietly accepting everything."""
    if not settings.contact_turnstile_secret and not settings.contact_allow_unverified:
        raise ContactRefused(503, "The contact form isn't available right now. Please email us instead.")


async def submit(
    pool: Any,
    *,
    name: str,
    email: str,
    company: Optional[str],
    topic: str,
    message: str,
    source_path: Optional[str],
    honeypot: str,
    turnstile_token: Optional[str],
    remote_ip: Optional[str],
    notify: Optional[Callable[..., Awaitable[bool]]] = None,
    verify: Optional[Callable[..., Awaitable[bool]]] = None,
) -> dict:
    """Runs the whole pipeline. Returns {"ok": True}; raises ContactRefused for anything that must not proceed."""
    if honeypot:
        return {"ok": True}  # silently dropped: nothing stored, nothing sent
    check_ready()

    name = clean_line(name, 120)
    email = (email or "").strip()
    company = clean_line(company, 160) or None
    topic = topic if topic in TOPICS else "general"
    message = (message or "").replace("\x00", "").strip()
    source = clean_line(source_path, 200) or None
    if not name:
        raise ContactRefused(422, "Please tell us your name.")
    if not valid_email(email):
        raise ContactRefused(422, "That email address doesn't look right.")
    if not message or len(message) > 4000:
        raise ContactRefused(422, "Please write a message of up to 4000 characters.")

    ip = remote_ip or "unknown"
    if not (_overall.allow("all") and _by_address.allow(ip) and _by_email.allow(email.lower())):
        raise ContactRefused(429, "Too many messages. Please try again later.")

    if settings.contact_turnstile_secret:
        ok = await (verify or verify_turnstile)(turnstile_token or "", remote_ip)
        if not ok:
            raise ContactRefused(403, "We couldn't confirm you're not a bot. Please try again.")

    lead_id = uuid7()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO contact_requests (id, name, email, company, topic, message, source_path) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                lead_id, name, email, company, topic, message, source,
            )
    except Exception:
        log.exception("contact request could not be stored")
        raise ContactRefused(503, "We couldn't save your message. Please email us instead.")

    sent = False
    try:
        sent = await (notify or send_notification)(
            lead_id=str(lead_id), name=name, email=email, company=company, topic=topic, message=message)
    except Exception:
        log.warning("contact notification failed for %s", lead_id)
    if sent:
        try:
            async with pool.acquire() as conn:
                await conn.execute("UPDATE contact_requests SET notified_at = now() WHERE id = $1", lead_id)
        except Exception:
            log.warning("could not mark %s notified", lead_id)
    return {"ok": True}


async def send_notification(*, lead_id: str, name: str, email: str, company: Optional[str], topic: str, message: str,
                            client: Optional[httpx.AsyncClient] = None) -> bool:
    """Plain-text email to the team alias through Resend. False (not an error) when email isn't configured."""
    key, to, sender = settings.resend_api_key, settings.contact_notify_to, settings.contact_notify_from
    if not (key and to and sender):
        return False
    body = (
        f"New contact request {lead_id}\n\nName: {name}\nEmail: {email}\nCompany: {company or '-'}\n"
        f"Topic: {topic}\n\n{message}\n"
    )
    payload = {"from": sender, "to": [to], "reply_to": email, "subject": "New kell contact request", "text": body}
    headers = {"Authorization": f"Bearer {key}"}
    if client is not None:
        r = await client.post(RESEND_URL, json=payload, headers=headers, timeout=8.0)
    else:
        async with httpx.AsyncClient() as c:
            r = await c.post(RESEND_URL, json=payload, headers=headers, timeout=8.0)
    return 200 <= r.status_code < 300
