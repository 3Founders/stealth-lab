"""Live-database proof that a Procedure ingested under an attribution license is served with its credit (needs
migration 126). Skipped unless DATABASE_URL is set AND local; each run uses its own license id."""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
_LOCAL = bool(DATABASE_URL) and (urlparse(DATABASE_URL).hostname in ("127.0.0.1", "localhost", "::1"))

pytestmark = pytest.mark.skipif(not _LOCAL, reason="needs a LOCAL DATABASE_URL with migration 126 (this test writes)")

VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


def test_find_ways_body_gets_the_credit_of_tagged_procedures_only():
    from app.db.session import create_pool
    from app.services.ingestion_context import open_ingestion_context
    from app.services.license_attribution import attach_attribution

    spdx = f"LicenseRef-attribution-test-{uuid.uuid4().hex[:8]}"
    notice = f"test notice {uuid.uuid4().hex[:8]}"

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            base = dict(source_type="skill_md", extractor_id="e2e", extractor_version="v1", actor_id="e2e",
                        scope_type="global")
            tagged = await open_ingestion_context(pool, license_spdx=spdx, attribution={"notice": notice}, **base)
            plain = await open_ingestion_context(pool, **base)

            async def proc(ctx: str) -> str:
                return await pool.fetchval(
                    "INSERT INTO procedures (name, goal, embedding, ingestion_context_id) "
                    "VALUES ($1, 'e2e goal', $2::vector, $3::uuid) RETURNING procedure_id::text",
                    f"e2e-attr-{uuid.uuid4().hex[:6]}", VECTOR, ctx)

            hit, miss = await proc(tagged), await proc(plain)
            body = {"procedures": [{"procedure_id": hit}], "related_examples": [{"procedure_id": miss}]}
            await attach_attribution(pool, body)
            assert body["procedures"][0]["attribution"] == notice
            assert "attribution" not in body["related_examples"][0]
        finally:
            await pool.close()

    asyncio.run(_run())
