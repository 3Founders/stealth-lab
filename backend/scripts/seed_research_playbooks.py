"""
Write the research playbooks (app/mcp_server/playbooks.py) into the global library as candidate procedures.

Same discipline as seed_canonical_coding_procedures.py: every row goes through capture_procedure() exactly as any caller's
would, verification_state='candidate' (nothing is born verified), no invented preconditions. The playbooks are also served
as MCP resources (stealth://playbooks), which work without this seed; this makes them findable through find_ways once the
library database the hosted server reads contains them.

Usage (from backend/):
    python scripts/seed_research_playbooks.py --dry-run     # print what would be written, touch nothing
    python scripts/seed_research_playbooks.py               # write to the database DATABASE_URL points at (production writes need the founder's go)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp_server.playbooks import PLAYBOOKS  # noqa: E402

DOMAIN = "research"
PROVENANCE = "prior_library"
CREATED_BY = "seed_research_playbooks"


async def seed(dry_run: bool) -> None:
    if dry_run:
        for p in PLAYBOOKS:
            print(f"[dry-run] would capture: {p.slug} ({len(p.steps)} steps + check) -- {p.goal}")
        print(f"\n[dry-run] {len(PLAYBOOKS)} playbooks, none written")
        return
    from app.db.session import create_pool
    from app.services.procedures import capture_procedure

    pool = await create_pool()
    try:
        for p in PLAYBOOKS:
            steps = [{"order": i, "goal": g} for i, g in enumerate(p.steps)]
            steps.append({"order": len(steps), "goal": "Check: " + p.check})
            result = await capture_procedure(
                pool, name=p.slug.replace("-", "_"), goal=p.goal, steps=steps, provenance=PROVENANCE, domain=DOMAIN,
                domain_payload={"playbook": True, "use_when": p.use_when, "seed_batch": "2026-10-28"},
                created_by=CREATED_BY, scope_type="entity")
            print(f"captured: {p.slug} -> procedure_id={result['procedure_id']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(seed(ap.parse_args().dry_run))
