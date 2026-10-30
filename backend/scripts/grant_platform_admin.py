"""Give a person the platform_admin role (the grant the admin dashboard's platform pages and billing need), or take it away.

    python scripts/grant_platform_admin.py --email you@example.com            # grant (dry run: shows who would get it)
    python scripts/grant_platform_admin.py --email you@example.com --apply    # really grant
    python scripts/grant_platform_admin.py --email you@example.com --revoke --apply

The person must have signed in to keळ at least once (that creates their users row), with the same email. The grant is a row in
platform_role_grants (revocable and audited by the table's own history); it is never read from a token claim. Uses $DATABASE_URL
(backend/.env is read the same way scripts/migrate.py reads it) or --dsn. Prints who it matched and never prints a connection string.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg


def _dsn_from_env_file() -> str | None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


async def run(dsn: str, email: str, apply: bool, revoke: bool, by: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT id::text AS id, email, display_name, is_active FROM users WHERE lower(email) = lower($1)", email)
        if not rows:
            print(f"No user with the email {email}. Sign in to the site once with that email, then run this again.")
            return 1
        for r in rows:
            held = await conn.fetchval("SELECT 1 FROM platform_role_grants WHERE user_id = $1::uuid AND role = 'platform_admin' AND t_expired IS NULL", r["id"])
            print(f"{r['email']} ({r['display_name'] or 'no name'}): {'active' if r['is_active'] else 'INACTIVE'}, platform_admin {'held' if held else 'not held'}")
            if not apply:
                continue
            if revoke:
                await conn.execute("UPDATE platform_role_grants SET t_expired = now() WHERE user_id = $1::uuid AND role = 'platform_admin' AND t_expired IS NULL", r["id"])
                print("  revoked")
            elif not held:
                await conn.execute(
                    "INSERT INTO platform_role_grants (user_id, role, granted_by) VALUES ($1::uuid, 'platform_admin', $2) "
                    "ON CONFLICT (user_id, role) DO UPDATE SET t_expired = NULL, granted_by = EXCLUDED.granted_by, t_created = now()", r["id"], by)
                print("  granted")
        if not apply:
            print("Dry run: nothing changed. Add --apply to make the change.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revoke", action="store_true")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--by", default="grant_platform_admin.py")
    a = ap.parse_args()
    dsn = a.dsn or os.environ.get("DATABASE_URL") or _dsn_from_env_file()
    if not dsn:
        print("ERROR: no DATABASE_URL in the environment or backend/.env, and no --dsn given.")
        return 2
    return asyncio.run(run(dsn, a.email, a.apply, a.revoke, a.by))


if __name__ == "__main__":
    sys.exit(main())
