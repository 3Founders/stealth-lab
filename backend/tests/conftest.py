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

# Point the live-DB suite at a throwaway Postgres without exporting the real
# DATABASE_URL.
#
# Every *_e2e.py here gates on os.environ["DATABASE_URL"] and, unset, skips.
# The obvious way to run them against a local `pgvector/pgvector:pg15`
# container instead of the production Supabase instance -- `DATABASE_URL=...
# pytest` -- also repoints anything else in the process that reads that var
# (app.config.settings, scripts imported transitively). TEST_DATABASE_URL is
# test-only: if it is set and DATABASE_URL is NOT already in the environment,
# promote it to DATABASE_URL here -- at conftest import, before
# DATABASE_URL_WAS_AMBIENT_AT_STARTUP is computed and before any .env load --
# so the whole suite sees it, the leak-guard below treats it as an explicit
# developer choice (not a .env regression), and nothing outside pytest is
# affected. An explicitly exported DATABASE_URL still wins.
#
# This is deliberately NOT an app.config setting: production code must never
# read a "test" database URL.
if "DATABASE_URL" not in os.environ and os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

# Captured once, at conftest import -- before pytest_configure, before any
# test module is collected, and before the wrapper below can run. True iff
# DATABASE_URL was ALREADY in the environment when this process started
# (an explicit `DATABASE_URL=... pytest` for the live-DB tests), as opposed
# to arriving later via a .env load. test_env_guard_offline.py uses this to
# distinguish "someone exported one by hand" (nothing for the guard to
# prove -- skip) from a real .env-leak regression (assert).
DATABASE_URL_WAS_AMBIENT_AT_STARTUP = "DATABASE_URL" in os.environ

# Same rationale, extended: an identity-provider config carried in
# backend/.env (SUPABASE_PROJECT_URL / OIDC_ISSUER / ...) must not silently
# change offline-test behaviour. app/api/deps.py::get_scope now branches on
# `oidc_configured(settings)` -- with a leaked SUPABASE_PROJECT_URL it would
# start REJECTING the X-Viewer-Id header that the header-driven offline
# router tests rely on. Strip these for a plain offline run; an explicitly
# exported value (a developer exercising the real auth path) is left alone.
_AUTH_ENV_KEYS = (
    "SUPABASE_PROJECT_URL", "SUPABASE_JWT_AUDIENCE",
    "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL",
)
_AUTH_ENV_AMBIENT_AT_STARTUP = {k for k in _AUTH_ENV_KEYS if k in os.environ}

_real_load_dotenv = dotenv.load_dotenv


def _load_dotenv_without_leaking_database_url(*args, **kwargs):
    had_database_url = "DATABASE_URL" in os.environ
    had_auth = {k for k in _AUTH_ENV_KEYS if k in os.environ}
    result = _real_load_dotenv(*args, **kwargs)
    if not had_database_url:
        os.environ.pop("DATABASE_URL", None)
    for k in _AUTH_ENV_KEYS:
        if k not in had_auth and k not in _AUTH_ENV_AMBIENT_AT_STARTUP:
            os.environ.pop(k, None)
    return result


def pytest_configure(config):
    dotenv.load_dotenv = _load_dotenv_without_leaking_database_url

    # pydantic-settings reads backend/.env DIRECTLY (env_file=), bypassing
    # dotenv.load_dotenv, so the wrapper above cannot stop SUPABASE_* /
    # OIDC_* from reaching `app.config.settings`. Null them on the singleton
    # for a plain offline run -- unless a developer exported one explicitly
    # to exercise the real auth path. get_scope()/authn read the singleton,
    # so this restores the pre-Supabase offline posture (X-Viewer-Id honoured)
    # without weakening any production check.
    try:
        from app import config as _cfg

        for _k, _attr in (
            ("SUPABASE_PROJECT_URL", "supabase_project_url"),
            ("SUPABASE_JWT_AUDIENCE", "supabase_jwt_audience"),
            ("OIDC_ISSUER", "oidc_issuer"),
            ("OIDC_AUDIENCE", "oidc_audience"),
            ("OIDC_JWKS_URL", "oidc_jwks_url"),
        ):
            if _k not in _AUTH_ENV_AMBIENT_AT_STARTUP and hasattr(_cfg.settings, _attr):
                try:
                    setattr(_cfg.settings, _attr, None)
                except Exception:  # noqa: BLE001 - frozen model: leave it
                    pass
    except Exception:  # noqa: BLE001 - config import failure surfaces elsewhere
        pass
