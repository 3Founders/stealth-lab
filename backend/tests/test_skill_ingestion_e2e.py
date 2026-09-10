"""
Phase 2 (prompts.md brief §16, "REAL LIVE TEST"): fetches a real vendored
SKILL.md fixture, runs the actual compiler, writes the actual current
`procedures` table, writes real task nodes, creates a real embedding,
preserves provenance, retrieves the imported procedure, and independently
verifies the actual persisted rows -- not just that the code printed
success. Same skip/self-cleaning convention as every other `*_e2e.py`
file in this repo.

This is agent D's deliverable from the peer session's own subagent split
(.scratch/phase2_ingestion_plan.md) -- that session hung before writing
it; landed here once the compiler itself (agents A-C) was independently
verified green.
"""
import asyncio
import os
from pathlib import Path

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder
from app.services.ingestion_sources.skill_md import LocalDirSkillSource
from app.services.skill_ingestion import run_skill_ingestion

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "skills" / "explore-repo"


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM procedures WHERE name = 'explore-unfamiliar-repository'")
    await pool.execute(
        "DELETE FROM ingested_artifacts WHERE uri LIKE '%explore-repo%'"
    )


def test_real_skill_md_ingestion_end_to_end():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            adapter = LocalDirSkillSource(str(FIXTURE_DIR))
            result = await run_skill_ingestion(
                pool, adapter, embedder=Embedder(), domain="coding",
                created_by="test_skill_ingestion_e2e",
            )
            metrics = result["metrics"]
            assert metrics["candidates"] == metrics["accepted"] + metrics["duplicates"] + metrics["rejected"] + metrics["stale"], (
                "manifest counts must add up (brief's own §14 contract)"
            )
            assert metrics["accepted"] == 1
            assert metrics["errors"] == 0

            # Independent verification -- query the real row, not the
            # compiler's own return value.
            row = await pool.fetchrow(
                "SELECT id, procedure_id, provenance, embedding IS NOT NULL AS has_embedding, "
                "verification_state, domain_payload "
                "FROM procedures WHERE name = 'explore-unfamiliar-repository'"
            )
            assert row is not None
            assert row["provenance"] == "prior_library"
            assert row["has_embedding"] is True, "brief §8: every ingested procedure must have a real embedding"
            assert row["verification_state"] == "candidate", "nothing is born verified"
            assert row["domain_payload"]["source"]["source_type"] == "skill_md"
            assert row["domain_payload"]["source"]["content_hash"]

            # V4-hardening B2 / rule 8 ("NO REUSABLE TASK ONTOLOGY"):
            # document ingestion no longer materialises a task_nodes row per
            # parsed step. task_nodes are execution-time only. The procedure's
            # own `steps` JSON carries the ordered actions; nothing is owed
            # to `task_nodes` at ingestion.
            edges = await pool.fetch(
                "SELECT target_id FROM edges WHERE source_id = $1 AND source_table = 'procedures' "
                "AND target_table = 'task_nodes'", row["id"],
            )
            assert edges == [], (
                "B2: skill_md ingestion must NOT manufacture task_nodes -- "
                f"found {len(edges)} DECOMPOSES_TO edges"
            )
            steps = row["domain_payload"].get("source", {})  # sanity: the row still exists
            assert steps.get("source_type") == "skill_md"

            # Real ingested_artifacts row (brief §7/§11 provenance).
            artifact = await pool.fetchrow(
                "SELECT content_hash, run_id FROM ingested_artifacts WHERE procedure_row_id = $1", row["id"],
            )
            assert artifact is not None
            assert str(artifact["run_id"]) == result["run_id"]

            # find_applicable_procedures can actually retrieve it (brief's exit condition).
            embedder = Embedder()
            query_vec = await embedder.embed_one("explore an unfamiliar repository", input_type="query")
            matches = await find_applicable_procedures(
                pool, goal_embedding=query_vec, require_verified=False, limit=5,
            )
            assert any(m["procedure_id"] == row["procedure_id"] for m in matches), (
                "the real, just-ingested procedure must be findable via real retrieval"
            )

            # Re-run with byte-identical content -- a no-op, no new procedure row.
            result2 = await run_skill_ingestion(
                pool, adapter, embedder=Embedder(), domain="coding",
                created_by="test_skill_ingestion_e2e",
            )
            assert result2["metrics"]["unchanged"] == 1
            count_after = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE name = 'explore-unfamiliar-repository'"
            )
            assert count_after == 1, "byte-identical re-ingestion must not create a duplicate row"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
