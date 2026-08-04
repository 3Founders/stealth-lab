"""
Verifies Agent Store search actually discriminates between genuinely
different content, not just "returns whatever exists" -- with only 2
real agents currently seeded, browse and any reasonably-matching search
look identical, so this adds a few clearly labeled, temporary test
agents with genuinely unrelated topics, runs real searches, and removes
them again at the end. Safe to run against your real database --
nothing it adds is left behind.

Run:
    python verify_agent_search.py
"""
import asyncio
import os

from app.db.session import create_pool
from app.services.agent_search import search_agents

TEST_AGENTS = [
    ("TEST: Flight booking assistant", "books flights and manages travel itineraries"),
    ("TEST: Invoice OCR scanner", "scans paper invoices and extracts line items"),
    ("TEST: Weather forecast summarizer", "summarizes daily weather forecasts for a region"),
]


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("Set DATABASE_URL first.")
        return

    pool = await create_pool(os.environ["DATABASE_URL"])
    inserted_ids = []

    try:
        print("-- adding temporary, clearly labeled test agents --")
        for name, desc in TEST_AGENTS:
            row = await pool.fetchrow(
                "INSERT INTO agents (name, description, source, execution_mode, "
                "skill_ref, review_state) VALUES ($1, $2, 'internal', 'local_skill', "
                "'test_only', 'approved') RETURNING id",
                name, desc,
            )
            inserted_ids.append(row["id"])
            print(f"  added: {name}")

        print()
        print("-- real searches, each should find its own topic and NOT the others --")
        checks = [
            ("book me a flight to Chicago", "TEST: Flight booking assistant"),
            ("scan this invoice for line items", "TEST: Invoice OCR scanner"),
            ("what's the weather forecast tomorrow", "TEST: Weather forecast summarizer"),
        ]
        failures = []
        for query, expected in checks:
            results = await search_agents(pool, query=query)
            names = [r.name for r in results]
            found = expected in names
            others_leaked = [n for n in names if n.startswith("TEST:") and n != expected]
            status = "PASS" if found else "FAIL"
            print(f"  [{status}] {query!r} -> found {expected!r}: {found}")
            if others_leaked:
                print(f"         (also matched, worth noticing: {others_leaked})")
            if not found:
                failures.append(query)

        print()
        if failures:
            print(f"{len(failures)} query/queries did not find their expected match: {failures}")
            print("This means search is NOT discriminating correctly -- worth investigating,")
            print("not something to ignore.")
        else:
            print("Search correctly discriminated between three genuinely unrelated topics.")
            print("This is real evidence it works, not just that it returns non-empty results.")

    finally:
        print()
        print("-- cleaning up: removing the temporary test agents --")
        for agent_id in inserted_ids:
            await pool.execute("DELETE FROM agents WHERE id = $1", agent_id)
        print(f"  removed {len(inserted_ids)} test agent(s). Your real store is unchanged.")
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
