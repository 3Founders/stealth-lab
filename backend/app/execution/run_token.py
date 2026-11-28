"""The credential an agent inside a run's sandbox uses to reach kell's models (app/execution/run_sandbox.py).

A website run executes in an Agent37 sandbox, and the agent there (OpenCode) needs a model. The person's own session
token must never enter that sandbox: the sandbox runs prompts written by a stranger, with internet access, so anything
inside it can be read and sent elsewhere. Instead the sandbox gets a token that can do exactly one thing:

    call  POST /v1/runs/{run_id}/chat/completions   for THIS run, while it is running, until it expires

It names the run and the owner, is signed with a key the sandbox never sees, and is checked against the run's row on
every call (app/mcp_server/chat_routes.py), so a finished, failed or cancelled run's token is dead even before it
expires. A leaked token therefore spends at most what one run could spend anyway, and only on model calls.

    kr1.<base64url {"r": run_id, "o": owner_id, "e": expiry epoch seconds}>.<base64url hmac-sha256>

The key is KELL_RUN_TOKEN_SECRET, or, when that is unset, one derived from ORG_KEY_ENCRYPTION_KEY with a fixed label
(domain separation: the derived key cannot decrypt anything, and nothing else signs with it). No key: no tokens, and
the sandbox path is off (run_sandbox.enabled()).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

PREFIX = "kr1."
DEFAULT_TTL_S = 3 * 3600          # one run's AI steps; a run that waits on a person gets a fresh token per step


class RunTokenInvalid(Exception):
    """Not a run token, a forged or altered one, or an expired one."""


@dataclass(frozen=True)
class RunClaims:
    run_id: str
    owner_id: str
    expires_at: int


def _key() -> bytes:
    own = (os.environ.get("KELL_RUN_TOKEN_SECRET") or "").strip()
    if own:
        return own.encode("utf-8")
    base = (os.environ.get("ORG_KEY_ENCRYPTION_KEY") or "").strip()
    if not base:
        return b""
    return hmac.new(base.encode("utf-8"), b"kell run token v1", hashlib.sha256).digest()


def available() -> bool:
    return bool(_key())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(run_id: str, owner_id: str, *, ttl_s: int = DEFAULT_TTL_S, now: Optional[float] = None) -> str:
    key = _key()
    if not key:
        raise RunTokenInvalid("no signing key is configured (KELL_RUN_TOKEN_SECRET or ORG_KEY_ENCRYPTION_KEY)")
    claims = {"r": str(run_id), "o": str(owner_id), "e": int((now if now is not None else time.time()) + ttl_s)}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64(hmac.new(key, (PREFIX + body).encode("ascii"), hashlib.sha256).digest())
    return f"{PREFIX}{body}.{sig}"


def is_run_token(token: str) -> bool:
    return bool(token) and token.startswith(PREFIX)


def verify(token: str, *, now: Optional[float] = None) -> RunClaims:
    key = _key()
    if not key or not is_run_token(token):
        raise RunTokenInvalid("not a run token")
    try:
        body, sig = token[len(PREFIX):].split(".", 1)
    except ValueError as exc:
        raise RunTokenInvalid("malformed run token") from exc
    want = _b64(hmac.new(key, (PREFIX + body).encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        raise RunTokenInvalid("bad signature")
    try:
        claims = json.loads(_unb64(body))
        out = RunClaims(str(claims["r"]), str(claims["o"]), int(claims["e"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise RunTokenInvalid("malformed run token") from exc
    if out.expires_at < int(now if now is not None else time.time()):
        raise RunTokenInvalid("this run token has expired")
    return out
