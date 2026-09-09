"""DEPRECATED -- thin wrapper around the canonical procedure re-embed.

This script used to carry its OWN embedding-text recipe
(``goal + "\\n\\n" + steps[0].action``), which is a competing, non-canonical
procedure representation. There is exactly one authoritative procedure
document representation now: ``retrieval_document.build_procedure_retrieval_document``
(``procdoc_v*``), and exactly one re-embed path that uses it:

    python scripts/backfill_procedure_embeddings.py --representation --provider gemini --force

That path is resumable, idempotent, atomic per row, keeps the previous
good vector on failure, and is rate/cost aware. This file now just calls
it so any runbook still referencing this path keeps working.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv

load_dotenv()

from backfill_procedure_embeddings import backfill_representation  # noqa: E402


async def _main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set")
        return 1
    print(
        "DEPRECATED: delegating to backfill_procedure_embeddings "
        "--representation --provider gemini --force (canonical builder, resumable)."
    )
    result = await backfill_representation(provider="gemini", force=True)
    print(f"done: {result}")
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
