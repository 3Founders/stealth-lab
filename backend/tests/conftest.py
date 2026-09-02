"""
Session-wide guard against a real DATABASE_URL leaking into offline test
collection.

Root cause (see test_mcp_check_procedure_offline.py's docstring for the
original discovery): importing app.mcp_server.server runs a module-level
load_dotenv() that mutates os.environ process-wide -- setting DATABASE_URL
(and any other credential this worktree's backend/.env carries) even though
no test asked for it. Two files gate on that value as a MODULE-LEVEL
constant captured at their own collection time (test_procedures_e2e.py's
`DATABASE_URL = os.environ.get("DATABASE_URL")`, same in
test_schema_drift.py) rather than a live re-check, so whether they see the
leak -- and therefore try to run as real live-DB tests instead of skipping
-- depends entirely on collection order relative to server.py's import.
test_mcp_check_procedure_offline.py's own env-snapshot/restore guard
protects os.environ itself but runs too late to help: it can only restore
os.environ AFTER its own import, which is no help to a module-level
constant some other file already cached before that point.

pytest_configure runs exactly once, before any test module is collected --
unlike a per-file guard, it can't lose an ordering race. It replaces
dotenv.load_dotenv with a wrapper that still does a real load (server.py's
own construction needs other .env values -- e.g. STEALTHLAB_MCP_TOKEN, or
its module-level `_require_mcp_token()` raises and collection errors out;
a full no-op of load_dotenv was tried first and broke exactly that), but
then removes DATABASE_URL again unless it was already present in the
environment *before* any .env load happened for the process. That leaves
an explicitly-exported DATABASE_URL (a developer running
`DATABASE_URL=... pytest tests/` by hand to exercise the live-DB tests)
untouched, while making sure `.env`'s own DATABASE_URL can never reach
os.environ for a plain offline run, regardless of which file happens to
import server.py first.
"""
import os

import dotenv

# Captured once, at conftest import -- before pytest_configure, before any
# test module is collected, and before the wrapper below can run. True iff
# DATABASE_URL was ALREADY in the environment when this process started
# (an explicit `DATABASE_URL=... pytest` for the live-DB tests), as opposed
# to arriving later via a .env load. test_env_guard_offline.py uses this to
# distinguish "someone exported one by hand" (nothing for the guard to
# prove -- skip) from a real .env-leak regression (assert).
DATABASE_URL_WAS_AMBIENT_AT_STARTUP = "DATABASE_URL" in os.environ

_real_load_dotenv = dotenv.load_dotenv


def _load_dotenv_without_leaking_database_url(*args, **kwargs):
    had_database_url = "DATABASE_URL" in os.environ
    result = _real_load_dotenv(*args, **kwargs)
    if not had_database_url:
        os.environ.pop("DATABASE_URL", None)
    return result


def pytest_configure(config):
    dotenv.load_dotenv = _load_dotenv_without_leaking_database_url
