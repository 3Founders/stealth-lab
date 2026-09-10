"""
G14 -- retrieval index-freshness contract, against a real Postgres
(migration 72 + `app.services.index_freshness`).

Asserts the lag signal is honest: a freshly (re)indexed procedure is
absent from `procedure_index_lag`; a canonical edit after indexing puts
it back with reason `stale_since_update`; re-marking it indexed clears
it; a row with no embedding reads `no_embedding`.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.index_freshness import get_index_lag, mark_procedure_indexed
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database index-lag test"
)

_MARK = "test-index-lag-e2e"


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{_MARK}%")


def _reason_for(rows, proc_id) -> str | None:
    for r in rows:
        if r["procedure_id"] == proc_id:
            return r["reason"]
    return None


def test_index_lag_tracks_reindex_and_canonical_drift():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)
            name = f"{_MARK}-{uuid.uuid4().hex[:8]}"
            res = await capture_procedure(
                pool, name=name, goal=name, provenance="system_pending_review",
                scope_type="global", steps=[{"order": 0, "goal": "x"}],
            )
            row_id = res["id"]
            proc_id = str(res["procedure_id"])

            # freshly captured, no embedding yet -> lag reason no_embedding
            lag = await get_index_lag(pool, limit=500)
            assert _reason_for(lag["sample"], proc_id) == "no_embedding"

            # give it an embedding + mark indexed -> clears
            await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, "
                "retrieval_document = 'doc', retrieval_document_version = 'procdoc_v2' "
                "WHERE id = $1",
                row_id, "[" + ",".join(["0.01"] * 1024) + "]",
            )
            await mark_procedure_indexed(pool, row_id)
            lag = await get_index_lag(pool, limit=500)
            assert _reason_for(lag["sample"], proc_id) is None, "indexed row must not lag"

            # a canonical edit after indexing -> stale_since_update
            await pool.execute(
                "UPDATE procedures SET goal = 'edited goal', updated_at = now() WHERE id = $1",
                row_id,
            )
            lag = await get_index_lag(pool, limit=500)
            assert _reason_for(lag["sample"], proc_id) == "stale_since_update"
            assert lag["lag_count"] >= 1

            # re-index -> clears again
            await mark_procedure_indexed(pool, row_id)
            lag = await get_index_lag(pool, limit=500)
            assert _reason_for(lag["sample"], proc_id) is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_recipe_drift_is_reported_without_a_view_migration():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)
            name = f"{_MARK}-drift-{uuid.uuid4().hex[:8]}"
            res = await capture_procedure(
                pool, name=name, goal=name, provenance="system_pending_review",
                scope_type="global", steps=[{"order": 0, "goal": "x"}],
            )
            row_id = res["id"]
            await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, retrieval_document = 'doc', "
                "retrieval_document_version = 'procdoc_v1', retrieval_indexed_at = now(), "
                "updated_at = now() WHERE id = $1",
                row_id, "[" + ",".join(["0.01"] * 1024) + "]",
            )
            lag = await get_index_lag(pool, limit=500)
            # timestamp-fresh, embedding present -> NOT in the view...
            assert _reason_for(lag["sample"], str(res["procedure_id"])) is None
            # ...but the recipe-drift check (service layer, no migration) sees it
            assert lag["recipe_drift_count"] >= 1
            assert lag["total_stale"] >= 1
            assert lag["current_recipe"] == "procdoc_v2"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
