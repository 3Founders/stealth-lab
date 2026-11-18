"""Read-only: what the 56,833 procedures actually are, before any delete decision."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

Q = {
    "by provenance": "SELECT provenance, count(*) n FROM procedures GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
    "by created_by": "SELECT created_by, count(*) n FROM procedures GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
    "by approval/availability": "SELECT approval_status, availability::text AS avail, count(*) n FROM procedures GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15",
    "dependent rows": "SELECT (SELECT count(*) FROM procedure_tool_cards) tool_cards, (SELECT count(*) FROM procedure_claim_refs) claim_refs, (SELECT count(*) FROM goal_tool_rankings) rankings, (SELECT count(*) FROM task_runs WHERE procedure_id IS NOT NULL) runs",
    "sample non-card procs": "SELECT id, name, provenance, created_by FROM procedures WHERE COALESCE(provenance,'') <> 'tool_card' ORDER BY t_created DESC LIMIT 15",
    "referencing tables": "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name WHERE tc.constraint_type='FOREIGN KEY' AND kcu.column_name LIKE '%procedure%' ORDER BY 1",
}


async def main():
    dsn = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=15)
    try:
        for label, sql in Q.items():
            try:
                rows = await pool.fetch(sql)
                print(f"--- {label} ({len(rows)})")
                for r in rows[:15]:
                    print("   ", {k: str(v)[:90] for k, v in dict(r).items()})
            except Exception as exc:
                print(f"--- {label}: {type(exc).__name__}: {str(exc)[:120]}")
    finally:
        await pool.close()


asyncio.run(main())
