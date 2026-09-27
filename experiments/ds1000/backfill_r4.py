"""Round 4: move round 3's verified examples (the experimental `procedures.verified_example` column) onto the
production provenance model (source_locator + a 'verified_solution' artifact), through verified_solutions.preserve.
Same Procedures, same code; only where it is stored changes, so find_ways returns it as `verified_solution`.

    KEL_DS1000_DSN=postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4 KNOWLEDGE_VERIFIED_EXAMPLES=true python backfill_r4.py
"""
from __future__ import annotations

import asyncio
import json

import demo_env


async def main() -> None:
    demo_env.verify_after_import()
    from app.config import settings
    from app.db.session import create_pool
    from app.services import verified_solutions

    assert settings.knowledge_verified_examples, "set KNOWLEDGE_VERIFIED_EXAMPLES=true"
    assert str(settings.database_url).endswith("/kel_ds1000_r4")
    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        rows = await pool.fetch("SELECT id::text, verified_example, owner_id, visibility FROM procedures "
                                "WHERE verified_example IS NOT NULL")
        done = 0
        for r in rows:
            ve = r["verified_example"] if isinstance(r["verified_example"], dict) else json.loads(r["verified_example"])
            ref = await verified_solutions.preserve(pool, procedure_row_id=r["id"], code=ve.get("code") or "",
                                                    task=ve.get("task") or "", language=ve.get("language") or "python",
                                                    verified_by=ve.get("verified_by"), owner_id=r["owner_id"],
                                                    visibility=r["visibility"] or "public")
            done += bool(ref)
        print(f"backfilled {done} of {len(rows)}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
