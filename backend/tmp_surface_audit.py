"""Read-only: do tool-card procedures actually surface? Costs, bindings, rankings, and the retrieval filter."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

Q = {
    "has_procedures flag": "SELECT has_procedures, count(*) n FROM goal_search_index GROUP BY 1",
    "toolcard procs w/ binding": ("SELECT count(*) FILTER (WHERE COALESCE(p.provenance,'')='tool_card') toolcard, "
                                  "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM procedure_tool_cards pc WHERE pc.procedure_id=p.id)) bound "
                                  "FROM procedures p WHERE p.t_invalid IS NULL AND p.availability='active'"),
    "card procs reachable": ("SELECT COALESCE(p.approval_status,'?') st, COALESCE(p.availability::text,'?') av, count(*) n "
                             "FROM procedures p WHERE COALESCE(p.provenance,'')='tool_card' AND p.t_invalid IS NULL GROUP BY 1,2 ORDER BY 3 DESC"),
    "gateway usable cards": ("SELECT count(*) total, count(*) FILTER (WHERE COALESCE(extra->>'connector_url','')<>'') with_connector "
                             "FROM tool_cards"),
    "goal_tool_rankings sample": "SELECT goal, count(*) n FROM goal_tool_rankings GROUP BY 1 ORDER BY 2 DESC LIMIT 8",
}


async def main():
    dsn = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=15)
    try:
        for label, sql in Q.items():
            try:
                rows = await pool.fetch(sql)
                print(f"--- {label}")
                for r in rows[:10]:
                    print("   ", {k: str(v)[:90] for k, v in dict(r).items()})
            except Exception as exc:
                print(f"--- {label}: {type(exc).__name__}: {str(exc)[:140]}")
    finally:
        await pool.close()


asyncio.run(main())
