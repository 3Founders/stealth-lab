"""The local REST API for testing the organization-key and join flow end to end: production code, a local Postgres, a local
test identity, no hosted database and no hosted identity provider.

Like run_backend.py (which serves the MCP app) this pins every database variable to a LOOPBACK database before the app is
imported, and trusts only the local test issuer (local_identity.py). It serves app.main:app, the REST API that has the
organization routes, /v1/join and the governed gateway /v1/chat/completions.

    python experiments/abtest/run_org_stack.py                  # serves http://127.0.0.1:8000
    python experiments/abtest/run_org_stack.py --mint cto       # prints nothing secret; writes .local/token-cto.txt

Defaults: database kel_orgtest on the scratch Postgres 127.0.0.1:55432 (ORG_TEST_DATABASE_URL overrides, loopback only),
ORG_KEY_ENCRYPTION_KEY generated once into .local/org_key.txt. Model keys are NOT read from here: the admin enters the
provider key through the API, exactly as in use.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"
DSN = os.environ.get("ORG_TEST_DATABASE_URL", "postgresql://kel_owner@127.0.0.1:55432/kel_orgtest")
PORT = int(os.environ.get("ORG_TEST_PORT", "8000"))

sys.path.insert(0, str(HERE))
import local_identity  # noqa: E402


def mint(name: str, days: int = 30) -> Path:
    """A token for a named test user (subject test-<name>); the same signing key as local_identity, so the same trust."""
    import jwt
    from cryptography.hazmat.primitives import serialization

    p = local_identity.ensure()
    key = serialization.load_pem_private_key(p["key"].read_bytes(), password=None)
    now = int(time.time())
    token = jwt.encode({"iss": local_identity.ISSUER, "aud": local_identity.AUDIENCE, "sub": f"test-{name}",
                        "email": f"{name}@stealthlab.invalid", "iat": now, "exp": now + days * 86400}, key,
                       algorithm="RS256", headers={"kid": local_identity.KID})
    out = local_identity.LOCAL / f"token-{name}.txt"
    out.write_text(token)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return out


def org_key() -> str:
    f = local_identity.LOCAL / "org_key.txt"
    if not f.exists():
        local_identity.LOCAL.mkdir(parents=True, exist_ok=True)
        f.write_text(base64.b64encode(os.urandom(32)).decode())
    return f.read_text().strip()


if __name__ == "__main__":
    if "--mint" in sys.argv:
        print(mint(sys.argv[sys.argv.index("--mint") + 1]))
        sys.exit(0)

    host = urlparse(DSN).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(f"refusing: ORG_TEST_DATABASE_URL must point at a loopback host, not {host!r}")
    for name in ("DATABASE_URL", "DATABASE_URL_DIRECT", "CONTROL_DATABASE_URL", "DATABASE_URL_LOCAL"):
        os.environ[name] = DSN
    os.environ["SEARCH_DATABASE_URL"] = ""
    os.environ.pop("DATABASE_URL_PREVIOUS", None)
    os.environ.setdefault("STEALTHLAB_ENV", "TEST")
    os.environ.setdefault("ENVIRONMENT", "TEST")
    os.environ.update(local_identity.server_env())
    os.environ["ORG_KEY_ENCRYPTION_KEY"] = org_key()
    os.environ["PUBLIC_API_URL"] = f"http://127.0.0.1:{PORT}"
    os.environ["FRONTEND_ORIGIN"] = "http://localhost:3200"
    os.environ["INGESTION_AUTO_ENABLED"] = "false"
    # organization keys are served only through the governed gateway, which governs only in shared mode (org_keys.py)
    os.environ["DEPLOYMENT_MODE"] = "shared"

    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, workers=1)
