"""HMAC signing shared by intent certificates and the audit chain (P1-06/P1-07).

The secret is `TOOL_CERT_SECRET`. A local checkout or scratch database usually has no secret, so
a stable development secret is derived from `SECRET_KEY` or `DATABASE_URL`; that keeps the manual
checks working without a ceremony. Production must set `TOOL_CERT_SECRET`.

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


def secret() -> bytes:
    """The signing key: `TOOL_CERT_SECRET`, or a derived development key."""
    value = os.environ.get("TOOL_CERT_SECRET", "").strip()
    if value:
        return value.encode("utf-8")
    seed = (os.environ.get("SECRET_KEY") or os.environ.get("DATABASE_URL") or "kell-dev").encode("utf-8")
    return hashlib.sha256(b"kell-dev-cert:" + seed).digest()


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
