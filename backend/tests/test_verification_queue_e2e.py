"""
G24 residual (audit doc): "independent global re-verification is
recorded-as-required but not executed". Proves the new durable review
queue (migration 74's `pending_global_verifications`, populated by
`publish_procedure`) records/lists/resolves correctly against real
Postgres, and -- the actual point of the founder's "review queue, not
auto-resolution" posture -- that resolving an entry NEVER touches
`procedures.verification_state`.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.procedures import capture_procedure
from app.services.verification_queue import (
    get_pending_global_verifications,
    record_pending_global_verification,
    resolve_pending_global_verification,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database verification-queue test"
)

_MARK = "test-verification-queue-e2e"


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM pending_global_verifications WHERE procedure_row_id IN "
        "(SELECT id FROM procedures WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_record_list_and_resolve_never_touches_verification_state():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        name = f"{_MARK}-{tag}"
        try:
            proc = await capture_procedure(
                pool, name=name, goal="a test procedure needing independent re-verification",
                steps=[{"order": 0, "goal": "do the thing"}],
                created_by="tester", visibility="public", scope_type="global",
                provenance="prior_library",
            )
            row_before = await pool.fetchrow(
                "SELECT verification_state FROM procedures WHERE id = $1::uuid", proc["id"],
            )
            assert row_before["verification_state"] == "candidate"

            entry_id = await record_pending_global_verification(
                pool, procedure_id=proc["procedure_id"], procedure_row_id=proc["id"],
                publication_id=None,
                reason="published without >=2 independent public verification groups",
                created_by="tester",
            )
            assert entry_id

            pending = await get_pending_global_verifications(pool, limit=1000)
            match = next((p for p in pending if p["id"] == uuid.UUID(entry_id)), None)
            assert match is not None
            assert match["procedure_name"] == name
            assert match["verification_state"] == "candidate"

            await resolve_pending_global_verification(
                pool, entry_id=entry_id, resolution="independently re-run by ops, confirmed",
                resolved_by="human-1",
            )

            row = await pool.fetchrow(
                "SELECT status, resolution, resolved_by, resolved_at "
                "FROM pending_global_verifications WHERE id = $1::uuid",
                uuid.UUID(entry_id),
            )
            assert row["status"] == "resolved"
            assert row["resolution"] == "independently re-run by ops, confirmed"
            assert row["resolved_by"] == "human-1"
            assert row["resolved_at"] is not None

            pending_after = await get_pending_global_verifications(pool, limit=1000)
            assert not any(p["id"] == uuid.UUID(entry_id) for p in pending_after)

            # the whole point: resolving the QUEUE entry must never itself
            # promote the procedure -- that stays a separate, real
            # evidence-based decision this module never makes.
            row_after = await pool.fetchrow(
                "SELECT verification_state FROM procedures WHERE id = $1::uuid", proc["id"],
            )
            assert row_after["verification_state"] == "candidate"
        finally:
            await _cleanup(pool, f"{_MARK}-")
            await pool.close()

    asyncio.run(_run())
