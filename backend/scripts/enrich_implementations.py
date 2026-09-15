#!/usr/bin/env python
"""
Real, resumable enrichment pass for `implementations` rows migration 80
left at `classification IN ('needs_enrichment', 'unclassified')` -- the
615 pre-existing rows that migration backfilled, plus any row a later
real ingestion run's LLM classification call transiently failed on.

Thin CLI over the real, already-tested backing function
(app/services/implementation_goals.py::enrich_pending_skill_package_implementations)
-- same "service function does the work, script is a thin CLI" discipline
scripts/backfill_procedure_embeddings.py already established for the
Procedure side of this substrate. No business logic here.

Usage (from backend/):
    python scripts/enrich_implementations.py --limit 50
    python scripts/enrich_implementations.py --limit 200 --model gemma-4-31B-it

Windows note (same as every other script in this repo): run with
`python`, not `python3`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.implementation_goals import enrich_pending_skill_package_implementations


def _extraction_client():
    """Same real client construction ingest_skills.py's own
    `_extraction_client()` uses -- duplicated rather than imported, same
    reasoning that function's own docstring gives: this is a standalone
    entry point that should not couple to that script's module."""
    try:
        from openai import OpenAI

        from app.config import settings

        key = settings.general_compute_api_key
        if not key:
            return None
        return OpenAI(max_retries=0, api_key=key, base_url=settings.general_compute_base_url)
    except Exception:  # noqa: BLE001 -- never block the CLI on this
        return None


async def _run(limit: int, model: str) -> dict:
    pool = await create_pool()
    try:
        return await enrich_pending_skill_package_implementations(
            pool, limit=limit, client=_extraction_client(), model=model,
        )
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=50, metavar="N",
        help="Maximum rows to attempt this run (default 50 -- each unclassified "
             "row that reaches the LLM path costs one real model call).",
    )
    parser.add_argument("--model", type=str, default="gemma-4-31B-it")
    args = parser.parse_args()

    counts = asyncio.run(_run(args.limit, args.model))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
