"""Live-database proof that Goals and Procedures written without a vector get one (2026-09-30: production had none).
Skipped unless DATABASE_URL is local."""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
_LOCAL = bool(DATABASE_URL) and (urlparse(DATABASE_URL).hostname in ("127.0.0.1", "localhost", "::1"))

pytestmark = pytest.mark.skipif(not _LOCAL, reason="needs a LOCAL DATABASE_URL (this test writes)")


class _Emb:
    dimension = 1024

    def __init__(self):
        self.texts: list[str] = []

    def embedding_model_id(self):
        return "test:sweep-1024"

    def _configured_provider(self):
        return "test"

    async def embed(self, texts, input_type="document"):
        self.texts += list(texts)
        return [[0.001 * (i + 1)] * self.dimension for i in range(len(texts))]


def test_goals_and_procedures_without_vectors_are_embedded_once():
    from app.db.session import create_pool
    from app.services.embedding_sweep import embed_goals, embed_procedures
    from app.services.goals import find_or_create_goal
    from app.services.procedures import capture_procedure

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        try:
            goal = await find_or_create_goal(pool, canonical_name=f"Rotate the sweep test logs {tag}", scope_type="global",
                                             provenance="system_pending_review", judge_mode="none", status="active")
            proc = await capture_procedure(pool, name=f"sweep test way {tag}", goal=f"Rotate the sweep test logs {tag}",
                                           steps=[{"order": 0, "do": "rotate the logs"}], provenance="system_pending_review",
                                           created_by="test", scope_type="global", judge_mode="none")
            assert await pool.fetchval("SELECT embedding IS NULL FROM goals WHERE id = $1::uuid", goal["id"])
            assert await pool.fetchval("SELECT embedding IS NULL FROM procedures WHERE id = $1::uuid", str(proc["id"]))
            emb = _Emb()
            assert await embed_goals(pool, emb, ids=[goal["id"]]) == 1
            assert await embed_procedures(pool, emb, ids=[str(proc["id"])]) == 1
            g = await pool.fetchrow("SELECT embedding_model_id, embedding IS NOT NULL AS has FROM goals WHERE id = $1::uuid",
                                    goal["id"])
            p = await pool.fetchrow("SELECT embedding_model_id, embedding IS NOT NULL AS has, retrieval_document_version, "
                                    "retrieval_indexed_at FROM procedures WHERE id = $1::uuid", str(proc["id"]))
            assert g["has"] and g["embedding_model_id"] == "test:sweep-1024"
            assert p["has"] and p["retrieval_document_version"] and p["retrieval_indexed_at"] is not None
            assert any(f"Rotate the sweep test logs {tag}" in t for t in emb.texts)
            # already embedded: never selected again
            assert await embed_goals(pool, emb, ids=[goal["id"]]) == 0
            assert await embed_procedures(pool, emb, ids=[str(proc["id"])]) == 0
            assert await pool.fetchval(
                "SELECT count(*) FROM projection_outbox WHERE object_id = ANY($1::uuid[]) AND status = 'pending'",
                [goal["id"], str(proc["procedure_id"])]) >= 1
        finally:
            await pool.close()

    asyncio.run(run())
