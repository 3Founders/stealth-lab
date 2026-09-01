"""
Live-database proving tests for app/services/capabilities.py. Requires a
real DATABASE_URL, skips (not fails) without one -- same pattern as
test_claim_evidence_e2e.py / test_implementation_registry_e2e.py.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.execution.implementation_registry import activate, register
from app.services.capabilities import (
    compare_implementations,
    get_implementation_capability,
    record_implementation_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "impl-cap-e2e"


async def _cleanup(pool) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'implementation' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")


def test_record_and_get_capability_reflects_real_recorded_evidence():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            impl = await register(
                pool, name=f"{PREFIX}-target", kind="tool", provider="graphify",
                created_by=PREFIX,
            )
            await activate(pool, impl["id"])

            # No evidence yet -- real zero, not a fabricated placeholder.
            empty = await get_implementation_capability(pool, impl["id"])
            assert empty["evidence_count"] == 0
            assert empty["p_estimate"] == 0.0

            await record_implementation_outcome(
                pool, implementation_id=impl["id"], outcome_status="success",
                success_criteria={"predicate": "the recorded run completed without error"},
                created_by=PREFIX,
            )
            await record_implementation_outcome(
                pool, implementation_id=impl["id"], outcome_status="success",
                success_criteria={"predicate": "second recorded run also completed"},
                created_by=PREFIX,
            )
            # A default-built failure carries direction='contradicts' and is
            # excluded from THIS estimate (see capabilities.py's own
            # docstring -- the exact real behavior mirrored from
            # procedure_graph_api.py's _capability_estimate). Real, not
            # hardcoded: recorded here to prove it does NOT silently move
            # the estimate, not that it does.
            await record_implementation_outcome(
                pool, implementation_id=impl["id"], outcome_status="failure",
                failure_class="environment_changed", created_by=PREFIX,
            )

            result = await get_implementation_capability(pool, impl["id"])
            assert result["evidence_count"] == 2
            assert result["success_count"] == 2
            assert result["p_estimate"] > 0.0
            assert result["level_gated"] is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_compare_implementations_orders_by_real_different_outcome_streams():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            good_impl = await register(
                pool, name=f"{PREFIX}-good", kind="tool", provider="graphify",
                created_by=PREFIX,
            )
            bad_impl = await register(
                pool, name=f"{PREFIX}-bad", kind="tool", provider="graphify",
                created_by=PREFIX,
            )
            await activate(pool, good_impl["id"])
            await activate(pool, bad_impl["id"])

            for _ in range(4):
                await record_implementation_outcome(
                    pool, implementation_id=good_impl["id"], outcome_status="success",
                    success_criteria={"predicate": "ok"}, created_by=PREFIX,
                )
            for _ in range(4):
                # Explicit direction='supports' so these real failure
                # outcomes actually enter the P estimate (rather than being
                # excluded like a default-direction failure would be, per
                # this module's own documented filter) -- proving
                # compare_implementations really orders on DIFFERENT real
                # outcome streams, not on "which one happens to have more
                # rows counted."
                await record_implementation_outcome(
                    pool, implementation_id=bad_impl["id"], outcome_status="failure",
                    direction="supports", failure_class="environment_changed",
                    created_by=PREFIX,
                )

            results = await compare_implementations(pool, [bad_impl["id"], good_impl["id"]])

            assert [r["implementation_id"] for r in results] == [good_impl["id"], bad_impl["id"]]
            assert results[0]["p_estimate"] > results[1]["p_estimate"]
            assert results[1]["success_count"] == 0
            assert results[1]["evidence_count"] == 4
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
