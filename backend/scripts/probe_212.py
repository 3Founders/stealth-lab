"""Read-only pre-flight for re-applying 212 to production.

212 drops and re-adds org_wallet_credits_source_check with 'referral_reward' added. If any existing row carries a source
value outside the new list, the ADD CONSTRAINT fails and the transaction aborts -- so check that first, and check what the
live table has today. Touches nothing.
"""
import asyncio
import os

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    print("--- org_wallet_credits_source_check as it is now ---")
    row = await conn.fetchrow(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'org_wallet_credits_source_check'")
    print(row["def"] if row else "(absent)")

    print("\n--- source values in use (must all be in the NEW list) ---")
    rows = await conn.fetch("SELECT source, count(*) AS n FROM org_wallet_credits GROUP BY source ORDER BY 2 DESC")
    for r in rows:
        print(f"  {r['source']!r}: {r['n']}")

    new_list = ("razorpay", "manual", "signup_bonus", "plan_included", "referral_reward")
    bad = [r["source"] for r in rows if r["source"] not in new_list]
    print(f"\nrows that would break the new constraint: {bad or 'none'}")

    print("\n--- referral_rewards already there? ---")
    cols = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name='referral_rewards' ORDER BY ordinal_position")
    print([c["column_name"] for c in cols] or "(table absent)")
    n = await conn.fetchval("SELECT count(*) FROM referral_rewards") if cols else None
    print(f"rows: {n}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
