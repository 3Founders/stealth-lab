"""Read-only: what the production ledger records for the recent migrations, and what the database actually has.
Run from backend/ with DATABASE_URL set. Touches nothing."""
import asyncio
import os
import sys

import asyncpg


async def main() -> None:
    url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(url)
    print("--- ledger, migrations 205+ ---")
    rows = await conn.fetch(
        "SELECT filename, checksum, applied_at FROM schema_migrations "
        "WHERE filename ~ '^(20[5-9]|21[0-9])_' ORDER BY filename")
    for r in rows:
        print(f"{r['filename']:42} {r['checksum'][:16]}  {r['applied_at']}")

    print("\n--- referral_rewards tables present? ---")
    tables = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND (table_name LIKE '%referral%') ORDER BY 1")
    print([t["table_name"] for t in tables] or "(none)")

    print("\n--- goal_submission_steps columns ---")
    cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='goal_submission_steps' ORDER BY ordinal_position")
    for c in cols:
        print(f"  {c['column_name']:22} {c['data_type']}")
    await conn.close()


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set")
    asyncio.run(main())
