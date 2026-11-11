"""HMAC signing shared by intent certificates and the audit chain (P1-06/P1-07).

The secret is `TOOL_CERT_SECRET`. A local checkout or scratch database usually has no secret, so
a fixed development key is used; that keeps the manual checks working without a ceremony.
Production must set `TOOL_CERT_SECRET`. Changing the secret invalidates every existing certificate
and audit receipt, so set it once and keep it.

Nothing here talks to the database. `canonical` is the one serialization both signers use, so a
row can be verified later without guessing how it was encoded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

GENESIS_HASH = "0" * 64
# A fixed development key. It is deliberately not derived from DATABASE_URL or SECRET_KEY: those
# differ between a process that loaded .env and one that did not, which silently breaks the chain.
# Production must set TOOL_CERT_SECRET; anything signed with this key is a local test.
DEV_SECRET = hashlib.sha256(b"kell-dev-cert").digest()


def secret() -> bytes:
    """The signing key: `TOOL_CERT_SECRET`, or the fixed development key."""
    value = os.environ.get("TOOL_CERT_SECRET", "").strip()
    if value:
        return value.encode("utf-8")
    return DEV_SECRET


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
