"""Create the restricted `stealth_app` role on every production database (securityp1.md §5.1 #5; runbook residual
risk #3). Same grants as scripts/sql/create_app_role.sql, applied with asyncpg so it can walk the control database, the
search members (S###) and the knowledge shards (K###).

    STEALTH_APP_DB_PASSWORD=<generated secret> python scripts/provision_app_role.py            # dry run: what it would do
    STEALTH_APP_DB_PASSWORD=<generated secret> python scripts/provision_app_role.py --apply    # create / update + verify

DSNs come from DATABASE_URL (control) and every S###/K###_DATABASE_URL in the environment or backend/.neon_shards.env.
Nothing it prints contains a connection string or the password: host names only.

AFTER it succeeds, the services must CONNECT as stealth_app for FORCE RLS to apply to them. That is an operator step on
the host: change each DATABASE_URL / S###_ / K###_DATABASE_URL user to stealth_app with this password. Keep the owner
credentials for scripts/migrate.py only (it needs DDL). Until then the services still connect as the table owner.

Generate the password with:  python -c "import secrets; print(secrets.token_urlsafe(32))"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]

STATEMENTS = [
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stealth_app') THEN
            CREATE ROLE stealth_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
        END IF;
    END $$""",
    # No `ALTER ROLE ... NOBYPASSRLS` here: managed Postgres (Neon) refuses attribute changes even to the role's
    # creator ("permission denied to alter role"). CREATE ROLE above sets them, and the flags are verified afterwards.
    "GRANT USAGE ON SCHEMA public TO stealth_app",
    "REVOKE CREATE ON SCHEMA public FROM stealth_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO stealth_app",
    "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO stealth_app",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO stealth_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stealth_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO stealth_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO stealth_app",
]


def targets() -> list[tuple[str, str]]:
    """(name, dsn) for the control database and every search member / knowledge shard."""
    env = dict(os.environ)
    shards = BACKEND / ".neon_shards.env"
    if shards.is_file():
        for line in shards.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([SK]\d{3}_DATABASE_URL)\s*=\s*(.+?)\s*$", line)
            if m and m.group(1) not in env:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if "DATABASE_URL" not in env:
        from dotenv import dotenv_values

        control = dotenv_values(BACKEND / ".env").get("DATABASE_URL")
        if control:
            env["DATABASE_URL"] = control
    out = [("control", env["DATABASE_URL"])] if env.get("DATABASE_URL") else []
    out += sorted((k[:4], v) for k, v in env.items() if re.fullmatch(r"[SK]\d{3}_DATABASE_URL", k))
    return out


async def one(name: str, dsn: str, password: str, apply: bool) -> str:
    import asyncpg

    host = urlparse(dsn).hostname or "?"
    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'stealth_app'")
        if not apply:
            return f"{name:8s} {host}: would {'update' if exists else 'create'} stealth_app"
        async with conn.transaction():
            for sql in STATEMENTS:
                await conn.execute(sql)
            # a password is a literal in ALTER ROLE; quote it with the server's own quoting, never by hand
            quoted = await conn.fetchval("SELECT quote_literal($1)", password)
            await conn.execute(f"ALTER ROLE stealth_app PASSWORD {quoted}")
            db = await conn.fetchval("SELECT current_database()")
            await conn.execute(f'GRANT CONNECT ON DATABASE "{db}" TO stealth_app')
        flags = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = 'stealth_app'")
        ok = not any(flags.values())
        return f"{name:8s} {host}: {'OK' if ok else 'FLAGS WRONG'} (superuser/bypassrls/createdb/createrole all false: {ok})"
    finally:
        await conn.close()


async def main_async(apply: bool, only: list[str]) -> int:
    password = os.environ.get("STEALTH_APP_DB_PASSWORD", "")
    if len(password) < 24:
        print("set STEALTH_APP_DB_PASSWORD to a generated secret of at least 24 characters (it is never printed)")
        return 2
    rc = 0
    for name, dsn in targets():
        if only and name not in only:
            continue
        try:
            print(await one(name, dsn, password, apply), flush=True)
        except Exception as exc:  # noqa: BLE001 -- report and continue; the summary exit code says it failed
            print(f"{name:8s} {urlparse(dsn).hostname or '?'}: FAILED ({type(exc).__name__})", flush=True)
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="create/update the role (default: dry run)")
    ap.add_argument("--only", nargs="*", default=[], help="limit to these names, e.g. control S001 K001")
    a = ap.parse_args()
    return asyncio.run(main_async(a.apply, a.only))


if __name__ == "__main__":
    sys.exit(main())
