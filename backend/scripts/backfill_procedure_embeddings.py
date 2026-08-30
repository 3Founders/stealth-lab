"""
Backfill embeddings for any live procedure missing one.

REAL BUG THIS FIXES (found via this session's own live 5-tool MCP test,
test_five_tool_mcp_surface_live.py): find_applicable_procedures() fills
its `limit` quota from ranked (embedded) survivors FIRST -- an
embedding-less procedure isn't just unranked, it can be completely
starved out of search results the moment the corpus has >= `limit`
other, irrelevant, embedded rows. The 25 canonical coding procedures
seeded by scripts/seed_canonical_coding_procedures.py were captured
before this was understood and have no embedding; this backfills them
(and anything else missing one) without touching any other column --
UPDATE only, no version bump, no re-verification reset.

Usage (from backend/):
    python scripts/backfill_procedure_embeddings.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.db.session import create_pool
from app.services.embeddings import Embedder, to_pgvector


async def backfill(dry_run: bool = False) -> None:
    pool = await create_pool()
    try:
        rows = await pool.fetch(
            "SELECT id, name, goal FROM procedures "
            "WHERE t_invalid IS NULL AND embedding IS NULL ORDER BY name"
        )
        print(f"{len(rows)} live procedure(s) missing an embedding")
        if dry_run:
            for r in rows:
                print(f"  [dry-run] would embed: {r['name']}")
            return

        embedder = Embedder()
        updated = 0
        for r in rows:
            vec = await embedder.embed_one(r["goal"], input_type="document")
            await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, "
                "embedding_model_id = $3, embedding_dim = $4 WHERE id = $1",
                r["id"], to_pgvector(vec), settings.embedding_model, settings.embedding_dimension,
            )
            print(f"  embedded: {r['name']}")
            updated += 1
        print(f"\n{updated}/{len(rows)} procedures backfilled")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))
