"""Did 212 take effect? Read-only. Asks for the constraint and columns by name rather than trusting a prior printout."""
import asyncio
import os

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    con = await conn.fetchval(
        "SELECT pg_get_constraintdef(pg_constraint.oid) FROM pg_constraint "
        "WHERE conname = 'referral_rewards_no_self'")
    print("no_self constraint:", con)
    cols = [c["column_name"] for c in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'referral_rewards' ORDER BY ordinal_position")]
    print("has reward_inr:", "reward_inr" in cols)
    print("columns:", cols)
    idx = [i["indexname"] for i in await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'referral_rewards' ORDER BY 1")]
    print("indexes:", idx)
    row = await conn.fetchrow(
        "SELECT checksum, applied_at FROM schema_migrations WHERE filename = '212_referral_rewards.sql'")
    print("ledger:", row["checksum"][:16], row["applied_at"])
    await conn.close()


asyncio.run(main())
