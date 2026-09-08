"""Regression: FetchingJwks must keep EC (ES256) signing keys.

Supabase Auth's modern signing key is EC / P-256 (alg ES256). An earlier
`_refresh` filtered the JWKS to `kty == "RSA"`, silently dropping the only
key, so every authenticated request 500'd with `KeyError(kid)` inside
`validate_token_async`. This pins that both asymmetric families survive
the fetch and a keyless entry is skipped.
"""
from __future__ import annotations

import asyncio
import io
import json

from app.services.authn import FetchingJwks


_JWKS = {
    "keys": [
        {"kid": "ec-1", "kty": "EC", "alg": "ES256", "crv": "P-256", "x": "a", "y": "b"},
        {"kid": "rsa-1", "kty": "RSA", "alg": "RS256", "n": "a", "e": "AQAB"},
        {"kty": "EC", "alg": "ES256", "crv": "P-256", "x": "c", "y": "d"},  # no kid -> skipped
        {"kid": "oct-1", "kty": "oct", "k": "sekret"},  # symmetric -> skipped
    ]
}


def _fake_urlopen(url, timeout=10):
    return io.BytesIO(json.dumps(_JWKS).encode("utf-8"))


def test_refresh_keeps_ec_and_rsa_drops_keyless_and_symmetric(monkeypatch):
    monkeypatch.setattr("app.services.authn.urllib.request.urlopen", _fake_urlopen)
    j = FetchingJwks("https://example.test/.well-known/jwks.json")

    ec = asyncio.run(j.get_key("ec-1"))
    assert ec["kty"] == "EC" and ec["alg"] == "ES256"

    rsa = asyncio.run(j.get_key("rsa-1"))
    assert rsa["kty"] == "RSA"

    assert set(j._keys or {}) == {"ec-1", "rsa-1"}  # keyless + oct not indexed


def test_unknown_kid_still_raises_keyerror(monkeypatch):
    monkeypatch.setattr("app.services.authn.urllib.request.urlopen", _fake_urlopen)
    j = FetchingJwks("https://example.test/.well-known/jwks.json")
    try:
        asyncio.run(j.get_key("nope"))
    except KeyError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected KeyError for an unknown kid")
