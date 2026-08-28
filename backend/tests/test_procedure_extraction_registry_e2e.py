"""
Real, live-database tests for procedure_extraction/registry.py. Same
pattern as the other e2e test files: requires a real DATABASE_URL, skips
(not fails) without one.
"""
import asyncio
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.procedure_extraction.registry import (
    approve_extractor,
    create_extractor_version,
    extractor_stats,
    reject_extractor,
    select_extractor,
    supersede_extractor_version,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

TEST_NAME = "regtest-extractor-001"


async def _cleanup(pool: asyncpg.Pool) -> None:
    await pool.execute("DELETE FROM procedures WHERE extracted_by LIKE $1", f"{TEST_NAME}%")
    await pool.execute("DELETE FROM procedure_extractors WHERE name = $1", TEST_NAME)


def test_grounded_hybrid_wins_the_tiebreak_and_deterministic_baseline_stays_selectable():
    """Migration 31 seeds grounded_hybrid_v1 tied on version ("1") with
    migration 20's deterministic_v1 baseline. select_extractor()'s own
    documented tiebreak (registry.py: prefer non-deterministic when
    version is tied) means an unrestricted scope now correctly resolves
    to grounded_hybrid_v1, not deterministic_v1 -- this is the fix for
    f7262a5's BLOCKER 1, not a regression of this test's original intent.
    Separately confirms deterministic_v1 is still a real, enabled,
    approved row -- it only stopped being the tiebreak winner, it was
    never retired."""
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            selected = await select_extractor(pool, current_scope={})
            assert selected is not None
            assert selected["name"] == "grounded_hybrid_v1"
            assert selected["kind"] == "llm"

            baseline = await pool.fetchrow(
                "SELECT enabled, review_state FROM procedure_extractors "
                "WHERE name = 'deterministic_v1' AND version = '1'",
            )
            assert baseline is not None
            assert baseline["enabled"] is True
            assert baseline["review_state"] == "approved"
        finally:
            await pool.close()

    asyncio.run(_run())


def test_proposed_extractor_is_not_selected():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            await create_extractor_version(
                pool, name=TEST_NAME, description="d", kind="llm", version="99",
            )
            selected = await select_extractor(pool, current_scope={})
            assert selected is None or selected["name"] != TEST_NAME, (
                "a proposed (not yet approved) extractor must never be selected, "
                "even at a very high version number"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_approved_but_not_enabled_is_not_selected():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            extractor_id = await create_extractor_version(
                pool, name=TEST_NAME, description="d", kind="llm", version="99",
            )
            await approve_extractor(pool, extractor_id=extractor_id, approver="tester", enable=False)
            selected = await select_extractor(pool, current_scope={})
            assert selected is None or selected["name"] != TEST_NAME
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_approved_and_enabled_high_version_is_preferred_over_baseline():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            extractor_id = await create_extractor_version(
                pool, name=TEST_NAME, description="d", kind="llm", version="99",
            )
            await approve_extractor(pool, extractor_id=extractor_id, approver="tester", enable=True)

            selected = await select_extractor(pool, current_scope={})
            assert selected is not None
            assert selected["name"] == TEST_NAME
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_supersede_removes_old_version_from_selection_but_keeps_the_row():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            v1_id = await create_extractor_version(
                pool, name=TEST_NAME, description="d", kind="llm", version="1",
            )
            await approve_extractor(pool, extractor_id=v1_id, approver="tester", enable=True)

            v2_id = await create_extractor_version(
                pool, name=TEST_NAME, description="d2", kind="llm", version="2",
            )
            await approve_extractor(pool, extractor_id=v2_id, approver="tester", enable=True)
            await supersede_extractor_version(pool, old_id=v1_id, new_id=v2_id)

            selected = await select_extractor(pool, current_scope={})
            assert selected["version"] == "2"

            # The old row must still exist -- provenance, not deleted.
            old_row = await pool.fetchrow(
                "SELECT id, t_invalid FROM procedure_extractors WHERE id = $1::uuid", v1_id,
            )
            assert old_row is not None
            assert old_row["t_invalid"] is not None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_scope_narrows_selection():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            extractor_id = await create_extractor_version(
                pool, name=TEST_NAME, description="d", kind="llm", version="99",
                scope={"domain": ["debugging"]},
            )
            await approve_extractor(pool, extractor_id=extractor_id, approver="tester", enable=True)

            wrong_scope = await select_extractor(pool, current_scope={"domain": ["visualization"]})
            assert wrong_scope is None or wrong_scope["name"] != TEST_NAME

            right_scope = await select_extractor(pool, current_scope={"domain": ["debugging"]})
            assert right_scope is not None
            assert right_scope["name"] == TEST_NAME
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_extractor_stats_with_no_procedures_produced_yet():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            stats = await extractor_stats(pool, extractor_id="nonexistent-id", name=TEST_NAME)
            assert stats["procedures_produced"] == 0
            assert stats["human_approval_rate"] is None
            assert stats["downstream_success_rate"] is None
        finally:
            await pool.close()

    asyncio.run(_run())
