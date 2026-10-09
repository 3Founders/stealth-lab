"""The local backend for the A-vs-C product test: production code, a local Postgres, no production database.

Every database variable is pinned to ABTEST_DATABASE_URL (default: the scratch Postgres on 127.0.0.1:55432,
database kel_abtest) BEFORE the app is imported, so pydantic-settings and server.py's load_dotenv of
.neon_shards.env (which never overrides a variable already set) cannot reach a hosted database:
  * DATABASE_URL / DATABASE_URL_DIRECT / CONTROL_DATABASE_URL -> the local test database;
  * SEARCH_DATABASE_URL = "" -> no separate search project (shards.search_database_url() reads "" as unset);
  * the test database's knowledge_shards registry holds only K000 (the control database itself), so no K###
    shard is ever opened, and it has no procedures: the global library is empty, as on a new customer's day one.
It refuses to start against anything but a loopback host. Model, judge and embedding keys still come from
backend/.env (JEV, OpenRouter embeddings, General Compute).

    python experiments/abtest/run_backend.py            # serves http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend")
DSN = os.environ.get("ABTEST_DATABASE_URL", "postgresql://kel_owner@127.0.0.1:55432/kel_abtest")
PORT = int(os.environ.get("ABTEST_PORT", "8765"))

host = urlparse(DSN).hostname or ""
if host not in ("127.0.0.1", "localhost", "::1"):
    sys.exit(f"refusing: ABTEST_DATABASE_URL must point at a loopback host, not {host!r}")

for name in ("DATABASE_URL", "DATABASE_URL_DIRECT", "CONTROL_DATABASE_URL", "DATABASE_URL_LOCAL"):
    os.environ[name] = DSN
os.environ["SEARCH_DATABASE_URL"] = ""
os.environ.pop("DATABASE_URL_PREVIOUS", None)
os.environ.setdefault("STEALTHLAB_ENV", "TEST")

# sign-in: trust only the local test identity (local_identity.py), never Supabase -- so the test user is a real,
# verified user (report_result, routing codes) whose token cannot be used against any hosted server
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_identity  # noqa: E402

local_identity.ensure()
os.environ.update(local_identity.server_env())

sys.path.insert(0, os.path.abspath(BACKEND))
os.chdir(os.path.abspath(BACKEND))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # the MCP server app: find_ways and the other tools at /mcp, plus /routing/codes and /triage. In its default
    # single_user mode it accepts backend/.env's STEALTHLAB_MCP_TOKEN, which does not expire mid-run.
    uvicorn.run("app.mcp_server.server:app", host="127.0.0.1", port=PORT, workers=1)
