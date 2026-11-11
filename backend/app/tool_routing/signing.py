"""HMAC signing shared by intent certificates and the audit chain (P1-06/P1-07).

The secret is `TOOL_CERT_SECRET`. A local checkout or scratch database usually has no secret, so a
fixed development key is allowed there -- but only there (review #29). It is used when `KELL_DEV=1`
is explicit, or when `DATABASE_URL` points at localhost; on any other deployment `secret()` raises
`SigningUnavailable` and the gate denies R2+ instead of signing with a key anyone with the repo
knows. Production must set `TOOL_CERT_SECRET`. Changing the secret invalidates every existing
certificate and audit receipt, so set it once and keep it.

Nothing here talks to the database. `canonical` is the one serialization both signers use, so a
row can be verified later without guessing how it was encoded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

GENESIS_HASH = "0" * 64
# A fixed development key. It is deliberately not derived from DATABASE_URL or SECRET_KEY: those
# differ between a process that loaded .env and one that did not, which silently breaks the chain.
# `dev_allowed()` below decides whether this key may be used at all.
DEV_SECRET = hashlib.sha256(b"kell-dev-cert").digest()
TRUTHY = ("1", "true", "yes", "on")
FALSY = ("0", "false", "no", "off")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


class SigningUnavailable(RuntimeError):
    """No `TOOL_CERT_SECRET` and this is not a development environment."""


def _flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in TRUTHY:
        return True
    if value in FALSY:
        return False
    return None


def _dsn() -> str:
    """The database this process would use: the environment first, then backend/.env."""
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values

        return str(dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("DATABASE_URL") or "").strip()
    except Exception:  # noqa: BLE001 -- no dotenv or no file: there is no local database to detect
        return ""


def _local_dsn(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        host = urlsplit(dsn).hostname
    except ValueError:
        return False
    return (host or "").strip().lower() in LOCAL_HOSTS


def dev_allowed() -> bool:
    """May the fixed development key be used? Only with an explicit `KELL_DEV` flag or a localhost DB."""
    flag = _flag("KELL_DEV")
    if flag is not None:
        return flag
    return _local_dsn(_dsn())


def secret() -> bytes:
    """The signing key: `TOOL_CERT_SECRET`, or the fixed development key on a local database.

    Raises `SigningUnavailable` when neither is available; callers must treat that as "cannot sign"
    (the gate denies R2+), never as a reason to fall back to a guessable key.
    """
    value = os.environ.get("TOOL_CERT_SECRET", "").strip()
    if value:
        return value.encode("utf-8")
    if dev_allowed():
        return DEV_SECRET
    raise SigningUnavailable(
        "TOOL_CERT_SECRET is not set and this is not a development database; "
        "set it once before issuing certificates or writing receipts")


def canonical(payload: dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, strings for anything not JSON-native."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sign(payload: dict[str, Any]) -> str:
    return hmac.new(secret(), canonical(payload), hashlib.sha256).hexdigest()


def verify(payload: dict[str, Any], signature: str) -> bool:
    return hmac.compare_digest(sign(payload), str(signature or ""))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical(payload if isinstance(payload, dict) else {"value": payload})).hexdigest()
