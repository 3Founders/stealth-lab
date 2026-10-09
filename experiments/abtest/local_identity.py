"""A local test identity for the A-vs-C test server: an RS256 key, its JWKS file, and one long-lived token for one
test user. The test server (run_backend.py) trusts ONLY this issuer, read from a file:// JWKS, so the token is
worthless anywhere else; the hosted servers trust Supabase, never this key. Files go to experiments/abtest/.local/
(git-ignored) and the token is never printed.

    python experiments/abtest/local_identity.py        # creates the key once; re-mints the token (60 days)
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

LOCAL = Path(__file__).resolve().parent / ".local"
ISSUER = "https://abtest.stealthlab.invalid/"
AUDIENCE = "stealthlab-abtest"
SUBJECT = "abtest-user"
KID = "abtest-1"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def paths() -> dict[str, Path]:
    return {"key": LOCAL / "signing_key.pem", "jwks": LOCAL / "jwks.json", "token": LOCAL / "token.txt"}


def ensure(days: int = 60) -> dict[str, Path]:
    p = paths()
    LOCAL.mkdir(parents=True, exist_ok=True)
    if not p["key"].exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        p["key"].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    key = serialization.load_pem_private_key(p["key"].read_bytes(), password=None)
    pub = key.public_key().public_numbers()
    p["jwks"].write_text(json.dumps({"keys": [{"kty": "RSA", "kid": KID, "alg": "RS256", "use": "sig",
                                               "n": _b64(pub.n), "e": _b64(pub.e)}]}))
    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "email": "abtest@stealthlab.invalid",
                        "iat": now, "exp": now + days * 86400}, key, algorithm="RS256", headers={"kid": KID})
    p["token"].write_text(token)
    for f in p.values():
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return p


def server_env() -> dict[str, str]:
    """What run_backend.py sets: this issuer instead of Supabase (both Supabase settings blank = not configured)."""
    return {"SUPABASE_PROJECT_URL": "", "SUPABASE_JWT_AUDIENCE": "", "OIDC_ISSUER": ISSUER, "OIDC_AUDIENCE": AUDIENCE,
            "OIDC_JWKS_URL": paths()["jwks"].resolve().as_uri()}


if __name__ == "__main__":
    out = ensure()
    print(f"key, JWKS and token written under {LOCAL} (token valid 60 days; not printed)")
