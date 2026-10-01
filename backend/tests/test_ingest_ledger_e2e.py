"""Live-database proof of the ingestion ledger (migration 127). Skipped unless DATABASE_URL is set AND local."""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
_LOCAL = bool(DATABASE_URL) and (urlparse(DATABASE_URL).hostname in ("127.0.0.1", "localhost", "::1"))

pytestmark = pytest.mark.skipif(not _LOCAL, reason="needs a LOCAL DATABASE_URL with migration 127 (this test writes)")


def test_outcomes_resume_and_one_identity_across_pipelines():
    from app.db.session import create_pool
    from app.ingest.common.ledger import FAILED, REJECTED, WRITTEN, ItemRef, Ledger

    tag = uuid.uuid4().hex[:8]

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            a = Ledger(pool, pipeline=f"test-a-{tag}", run_id=str(uuid.uuid4()), target="local", max_attempts=2)
            b = Ledger(pool, pipeline=f"test-b-{tag}", run_id=str(uuid.uuid4()), target="local")
            ident = f"identity:{tag}"
            ref_a = ItemRef("i1", "hf:x", "0" * 40, "r1", ident)
            ref_b = ItemRef("j1", "hf:y", "1" * 40, "r9", ident)

            # failed is retried until max_attempts, then skipped
            assert await a.should_process("i1") == (True, None)
            await a.record(ref_a, FAILED, "network", detail={"error": "x"})
            assert await a.should_process("i1") == (True, None)
            await a.record(ref_a, FAILED, "network")
            assert await a.should_process("i1") == (False, "failed_max_attempts")

            # an infrastructure failure (model capacity, network) never uses up the item's attempts
            ref_rl = ItemRef("i2", "hf:x", "0" * 40, "r2", f"identity2:{tag}")
            for _ in range(3):
                await a.record(ref_rl, FAILED, "RateLimitError", detail={"error": "429"})
            assert await a.should_process("i2") == (True, None)
            assert "i2" not in await a.settled_keys() and "i1" in await a.settled_keys()

            # written is terminal and owns the identity in every pipeline
            await a.record(ref_a, WRITTEN, "written", detail={"n": 1}, objects={"procedure_ids": ["p"]})
            assert await a.should_process("i1") == (False, "already_written")
            assert (await b.identity_owner(ident))["item_key"] == "i1"

            # a second pipeline writing the same identity loses the race and is recorded as a rejection
            stored = await b.record(ref_b, WRITTEN, "written", objects={"procedure_ids": ["q"]})
            assert stored == REJECTED
            row = await pool.fetchrow("SELECT status, reason, jsonb_typeof(detail) AS t, detail->'orphaned_objects' AS o "
                                      "FROM ingest_ledger WHERE pipeline = $1 AND item_key = 'j1'", b.pipeline)
            assert row["status"] == REJECTED and row["reason"] == "duplicate_identity" and row["t"] == "object"
            assert row["o"] is not None
        finally:
            await pool.execute("DELETE FROM ingest_ledger WHERE pipeline LIKE $1", f"test-%-{tag}")
            await pool.close()

    asyncio.run(_run())
