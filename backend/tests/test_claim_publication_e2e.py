"""
The private -> global publish gate for Claims (`app.services.claim_publication`).

The end-to-end story this proves is the one the founder is validating:
a private candidate captured locally (here, via a resolved `.stealth/`
exploration -- G12) sits private until an explicit, audited,
dependency-checked publish promotes it to global -- never automatically.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import pytest

from app.db.session import create_pool
from app.services.claim_publication import (
    ClaimAlreadyPublic,
    ClaimNotFound,
    ClaimPublicationDenied,
    publish_claim,
)
from app.services.claims import capture_claim
from app.services.sources import register_source
from app.stealth.exploration import close_exploration, open_exploration

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database claim-publish test"
)

_MARK = "test-claim-publication-e2e"


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1",
                       f"{name_prefix}%")


def test_owner_publishes_a_clean_private_claim_to_global():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        statement = f"{_MARK}: parallel builds cut CI time in half {tag}"
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-public-src-{tag}",
                created_by="owner-1", visibility="public", owner_id="owner-1", scope_type="global",
            )
            claim_id = await capture_claim(
                pool, statement=statement, task_ids=[], source_ref=src["id"],
                created_by="owner-1", owner_id="owner-1", visibility="private", scope_type="global",
            )
            assert claim_id

            row = await pool.fetchrow("SELECT visibility FROM knowledge_nodes WHERE id=$1", claim_id)
            assert row["visibility"] == "private"

            result = await publish_claim(pool, claim_id=claim_id, actor_subject="owner-1")
            assert result["previous_visibility"] == "private"

            row = await pool.fetchrow("SELECT visibility FROM knowledge_nodes WHERE id=$1", claim_id)
            assert row["visibility"] == "public"

            cs = await pool.fetchrow(
                "SELECT cso.detail FROM change_sets cs "
                "JOIN change_set_operations cso ON cso.change_set_id = cs.id "
                "WHERE cso.target_id = $1 ORDER BY cs.created_at DESC LIMIT 1",
                str(claim_id),
            )
            assert cs is not None, "publish must be audited via a ChangeSet"

            # idempotent-by-refusal: publishing again is a caller error, not a silent no-op
            with pytest.raises(ClaimAlreadyPublic):
                await publish_claim(pool, claim_id=claim_id, actor_subject="owner-1")
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.close()

    asyncio.run(_run())


def test_non_owner_cannot_publish_someone_elses_private_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        statement = f"{_MARK}: owner-only claim {tag}"
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-public-src2-{tag}",
                created_by="owner-2", visibility="public", owner_id="owner-2", scope_type="global",
            )
            claim_id = await capture_claim(
                pool, statement=statement, task_ids=[], source_ref=src["id"],
                created_by="owner-2", owner_id="owner-2", visibility="private", scope_type="global",
            )
            with pytest.raises(ClaimPublicationDenied) as exc:
                await publish_claim(pool, claim_id=claim_id, actor_subject="someone-else")
            assert any("does not own" in r for r in exc.value.reasons)

            row = await pool.fetchrow("SELECT visibility FROM knowledge_nodes WHERE id=$1", claim_id)
            assert row["visibility"] == "private", "a denied publish must not mutate anything"
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.close()

    asyncio.run(_run())


def test_claim_citing_a_private_source_cannot_be_published():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        statement = f"{_MARK}: derived from a private source {tag}"
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-private-src-{tag}",
                created_by="owner-3", visibility="private", owner_id="owner-3", scope_type="global",
            )
            claim_id = await capture_claim(
                pool, statement=statement, task_ids=[], source_ref=src["id"],
                created_by="owner-3", owner_id="owner-3", visibility="private", scope_type="global",
            )
            with pytest.raises(ClaimPublicationDenied) as exc:
                await publish_claim(pool, claim_id=claim_id, actor_subject="owner-3")
            assert any("cannot generalize" in r for r in exc.value.reasons)
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.execute("UPDATE sources SET t_invalid = now() WHERE locator LIKE $1", f"{_MARK}%")
            await pool.close()

    asyncio.run(_run())


def test_publish_nonexistent_claim_raises_not_found():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with pytest.raises(ClaimNotFound):
                await publish_claim(pool, claim_id=str(uuid.uuid4()), actor_subject="anyone")
        finally:
            await pool.close()

    asyncio.run(_run())


def test_exploration_resolution_to_publish_end_to_end():
    """The full private-candidate -> explicit-publish story: a locally
    resolved exploration becomes a private Claim (G12), which stays
    private until this explicit call promotes it."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        question = f"{_MARK}: is retry-with-backoff already implemented {tag}?"
        try:
            with tempfile.TemporaryDirectory() as ws:
                eid = open_exploration(ws, owner="owner-4", question=question)
                claim_id = await close_exploration(
                    ws, eid, status="RESOLVED", resolution="yes, in http_client.py",
                    pool=pool, created_by="owner-4", owner_id="owner-4",
                )
                assert claim_id

                row = await pool.fetchrow("SELECT visibility FROM knowledge_nodes WHERE id=$1", claim_id)
                assert row["visibility"] == "private"

                result = await publish_claim(pool, claim_id=claim_id, actor_subject="owner-4")
                assert result["previous_visibility"] == "private"

                row = await pool.fetchrow("SELECT visibility FROM knowledge_nodes WHERE id=$1", claim_id)
                assert row["visibility"] == "public"
        finally:
            await _cleanup(pool, f"{question[:40]}")
            await pool.close()

    asyncio.run(_run())
