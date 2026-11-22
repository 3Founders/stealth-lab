"""Read-only: what type is procedures.owner_id, and who owns the existing kell-authored procedures."""
import asyncio
import os

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    col = await conn.fetchrow(
        "SELECT data_type, udt_name FROM information_schema.columns "
        "WHERE table_name='procedures' AND column_name='owner_id'")
    print("procedures.owner_id:", dict(col) if col else "(absent)")
    rows = await conn.fetch(
        "SELECT created_by, owner_id::text AS owner, visibility, count(*) AS n FROM procedures "
        "GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 8")
    for r in rows:
        print(f"  created_by={r['created_by']!r:24} owner={r['owner']!r:40} {r['visibility']:8} n={r['n']}")
    await conn.close()


asyncio.run(main())
