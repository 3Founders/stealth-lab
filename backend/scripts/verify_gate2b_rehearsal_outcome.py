"""One-off verification of the gate-2b rehearsal's recorded outcome."""
import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

import asyncpg

PROCEDURE_ID = "4b60e1ad-c092-4b8b-96f3-19bd85e1c31b"


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        row = await conn.fetchrow(
            "SELECT version, verification_state, verification_stats, "
            "embedding IS NOT NULL AS has_embedding "
            "FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL",
            PROCEDURE_ID,
        )
        print("live row:", json.dumps(dict(row), default=str))
        stats = await conn.fetch(
            "SELECT success, count(*) AS n FROM execution_outcomes "
            "WHERE procedure_id = $1::uuid GROUP BY success",
            PROCEDURE_ID,
        )
        for s in stats:
            print("execution_outcomes:", dict(s))
    finally:
        await conn.close()


asyncio.run(main())
