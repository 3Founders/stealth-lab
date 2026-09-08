"""
Backfill a single, auditable embedding space for live procedures.

REAL BUG THIS FIXES (found via this session's own live 5-tool MCP test,
test_five_tool_mcp_surface_live.py): find_applicable_procedures() fills
its `limit` quota from ranked (embedded) survivors FIRST -- an
embedding-less procedure isn't just unranked, it can be completely
starved out of search results the moment the corpus has >= `limit`
other, irrelevant, embedded rows. The 25 canonical coding procedures
seeded by scripts/seed_canonical_coding_procedures.py were captured
before this was understood and have no embedding; this backfills them
(and anything else missing one) without touching any other column --
UPDATE only, no version bump, no re-verification reset.  `--replace-existing`
is deliberately explicit: use it after selecting one provider/model to repair
a corpus that was previously embedded in mixed vector spaces.

Usage (from backend/):
    python scripts/backfill_procedure_embeddings.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import create_pool
from app.services.embeddings import Embedder, to_pgvector


def _embedding_text(row) -> str:
    """Match structured-skill ingestion's goal + abstract-workflow input."""
    steps = row["steps"] or []
    step_text = [
        str(step.get("goal") or step.get("description") or "")
        for step in steps if isinstance(step, dict)
    ]
    return " ".join([
        row["capability_statement"] or row["goal"], "Workflow:", *step_text,
    ])


async def backfill(
    dry_run: bool = False, *, replace_existing: bool = False,
    source_id: str | None = None, limit: int | None = None,
) -> None:
    pool = await create_pool()
    try:
        clauses = ["t_invalid IS NULL", "($1::boolean OR embedding IS NULL)"]
        if source_id is not None:
            clauses.append("domain_payload->'source'->>'source_id' = $2")
        where = " AND ".join(clauses)
        params = [replace_existing]
        if source_id is not None:
            params.append(source_id)
        suffix = " ORDER BY name"
        if limit is not None:
            suffix += f" LIMIT {int(limit)}"
        rows = await pool.fetch(
            "SELECT id, name, goal, capability_statement, steps FROM procedures WHERE "
            + where + suffix,
            *params,
        )
        action = "re-embed" if replace_existing else "embed"
        print(f"{len(rows)} live procedure(s) selected to {action}")
        if dry_run:
            for r in rows:
                print(f"  [dry-run] would {action}: {r['name']}")
            return

        embedder = Embedder(rate_limit_pool=pool)
        updated = 0
        for r in rows:
            vec, metadata = await embedder.embed_one_with_metadata(
                _embedding_text(r), input_type="document",
            )
            await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, "
                "embedding_model_id = $3, embedding_dim = $4, embedding_provider = $5, "
                "embedding_input_type = $6, embedding_text_hash = $7, "
                "domain_payload = jsonb_set(COALESCE(domain_payload, '{}'::jsonb), "
                "'{embedding}', $8::jsonb, true) WHERE id = $1",
                r["id"], to_pgvector(vec), metadata.model_id, metadata.dimension,
                metadata.provider, metadata.input_type, metadata.text_sha256,
                json.dumps(metadata.__dict__),
            )
            past_tense = "re-embedded" if replace_existing else "embedded"
            print(f"  {past_tense}: {r['name']}")
            updated += 1
        print(f"\n{updated}/{len(rows)} procedures updated")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace-existing", action="store_true",
        help="Re-embed existing vectors into the configured one-provider space.",
    )
    parser.add_argument("--source-id", help="Limit repair to one structured source id.")
    parser.add_argument("--limit", type=int, help="Bound this invocation for worker-safe repair.")
    args = parser.parse_args()
    asyncio.run(backfill(
        dry_run=args.dry_run, replace_existing=args.replace_existing,
        source_id=args.source_id, limit=args.limit,
    ))
