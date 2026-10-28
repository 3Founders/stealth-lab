"""Unsubscribe links: a token that cannot be forged and needs no lookup to check.

token = base64url(email) + "." + base64url(HMAC-SHA256(UNSUBSCRIBE_SECRET, email))

The link goes to the Supabase Edge Function in supabase_functions/unsubscribe (which holds the same secret as a function secret). RFC 8058
one-click: the mail carries `List-Unsubscribe: <that link>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; the mail program POSTs to
the link and the function marks the address unsubscribed. A person who opens the link in a browser (GET) sees a page with one button,
because link scanners fetch every link in a mail and must not unsubscribe people by accident.

Caveat that code cannot remove: RFC 8058 wants the mail's DKIM signature to cover both List-Unsubscribe headers. The mail provider (GoDaddy)
signs the mail, not us, and we cannot see which headers it signs. Providers that do not see a covering signature may not show their
unsubscribe button; the link in the footer and the reply-to-unsubscribe path still work.
"""
from __future__ import annotations

import base64
import hashlib
import hmac


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(secret: str, email: str) -> str:
    e = email.strip().lower().encode()
    return _b64(e) + "." + _b64(hmac.new(secret.encode(), e, hashlib.sha256).digest())


def verify(secret: str, token: str) -> str | None:
    """The address in a genuine token, else None."""
    try:
        left, right = token.split(".", 1)
        e = _unb64(left)
        if hmac.compare_digest(hmac.new(secret.encode(), e, hashlib.sha256).digest(), _unb64(right)):
            return e.decode()
    except Exception:  # noqa: BLE001 -- anything malformed is simply not valid
        pass
    return None


def link(base_url: str, secret: str, email: str) -> str:
    if not base_url.startswith("https://"):
        raise ValueError("UNSUBSCRIBE_URL must be an https address")
    return f"{base_url}{'&' if '?' in base_url else '?'}t={sign(secret, email)}"
