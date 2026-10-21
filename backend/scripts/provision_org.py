#!/usr/bin/env python3
"""Create a customer organisation and make one person its owner.

There is no self-serve sign-up: an operator runs this once per customer, then the owner signs in to the admin console and
does the rest (connect the company's provider key, apply the policy, invite developers).

The owner must already have signed in once (that is what creates their `users` row, keyed by the identity provider's
subject); this looks them up by email and refuses if there is no such person, rather than inventing an identity.

    python scripts/provision_org.py --name "Example Company" --slug example --owner-email cto@example.com
    python scripts/provision_org.py ... --dry-run          # show what would change

Re-running is safe: an existing slug is reused, and an existing owner grant is left alone. It creates no policy: until the
owner applies one, every provider call for the organisation is denied (db/136).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OWNER_ROLE_ID = "10000000-0000-0000-0000-000000000001"
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")


async def provision(conn, *, name: str, slug: str, owner_email: str, dry_run: bool) -> dict:
    if not SLUG.fullmatch(slug):
        raise SystemExit("--slug must be 2-41 characters of a-z, 0-9 or '-', starting with a letter or digit")
    users = await conn.fetch("SELECT id::text AS id, display_name FROM users WHERE lower(email) = lower($1) "
                             "AND is_active AND t_expired IS NULL", owner_email)
    if not users:
        raise SystemExit(f"no active user with email {owner_email!r}: ask them to sign in to the admin console once, then re-run")
    if len(users) > 1:
        raise SystemExit(f"{len(users)} users share the email {owner_email!r}; pass one that is unique")
    owner = users[0]
    org = await conn.fetchrow("SELECT id::text AS id, name FROM organizations WHERE slug = $1 AND t_expired IS NULL", slug)
    out = {"slug": slug, "owner": owner["id"], "organization_exists": org is not None}
    if dry_run:
        return {**out, "dry_run": True}
    async with conn.transaction():
        if org is None:
            org = await conn.fetchrow("INSERT INTO organizations (name, slug) VALUES ($1, $2) RETURNING id::text AS id, name",
                                      name, slug)
        granted = await conn.fetchval(
            "INSERT INTO org_memberships (organization_id, user_id, role_id, granted_by) "
            "VALUES ($1::uuid, $2::uuid, $3::uuid, NULL) ON CONFLICT DO NOTHING RETURNING 1", org["id"], owner["id"],
            OWNER_ROLE_ID)
    return {**out, "organization_id": org["id"], "owner_granted": bool(granted)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--owner-email", required=True)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dsn:
        raise SystemExit("set DATABASE_URL or pass --dsn")
    import asyncpg

    conn = await asyncpg.connect(a.dsn)
    try:
        print(await provision(conn, name=a.name, slug=a.slug, owner_email=a.owner_email, dry_run=a.dry_run))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
