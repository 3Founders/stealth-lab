"""
Real, live-database proving tests for task #35 (`app/services/claim_impact.py`):
"a claim changes -> does any procedure's precondition rely on it -> mark it
stale via the real, existing `mark_procedure_stale`."

Same pattern as every other `*_e2e.py` file in this suite (see
`test_claim_relations_e2e.py`, `test_procedures_e2e.py`): requires a real
DATABASE_URL, skips (not fails) without one. Uses real `capture_procedure`
with a precondition built via
`procedure_extraction.derive.precondition_with_claim` carrying a real
`claim_id`, real `find_procedures_referencing_claim` proving it finds
exactly that procedure and not an unrelated one, and real
`propagate_claim_change` proving the procedure's `staleness` column
actually flips to 'stale' via the real `mark_procedure_stale` -- no
reimplementation of staleness here.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.claim_impact import (
    find_procedures_referencing_claim,
    propagate_claim_change,
)
from app.services.procedure_extraction.derive import precondition_with_claim
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-impact-e2e"


async def _cleanup(pool) -> None:
    # change_sets is append-only (Band 1.9c, invariant #19) -- real
    # ChangeSet rows this test triggers via mark_procedure_stale are
    # intentionally left in place, same as every other *_e2e.py file that
    # exercises a ChangeSet-recording path.
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")


def test_find_procedures_referencing_claim_matches_exactly_the_referencing_row():
    """A procedure whose precondition carries the target claim_id is
    found; an unrelated procedure with a different claim_id (or no
    claim_id at all) is not."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            target_claim_id = str(uuid.uuid4())
            other_claim_id = str(uuid.uuid4())

            referencing = await capture_procedure(
                pool, name=f"{PREFIX}-referencing", goal="g",
                preconditions=[
                    precondition_with_claim(
                        "repo", "has_version", "2.0", claim_id=target_claim_id,
                    ),
                ],
                provenance="system_pending_review", scope_type="global",
            )
            unrelated = await capture_procedure(
                pool, name=f"{PREFIX}-unrelated", goal="g",
                preconditions=[
                    precondition_with_claim(
                        "repo", "has_version", "1.0", claim_id=other_claim_id,
                    ),
                ],
                provenance="system_pending_review", scope_type="global",
            )
            no_claim = await capture_procedure(
                pool, name=f"{PREFIX}-no-claim", goal="g",
                preconditions=[{"subject": "repo", "predicate": "has_version", "object": "3.0"}],
                provenance="system_pending_review", scope_type="global",
            )

            found = await find_procedures_referencing_claim(pool, target_claim_id)
            found_ids = {row["id"] for row in found}

            assert referencing["id"] in found_ids
            assert unrelated["id"] not in found_ids
            assert no_claim["id"] not in found_ids
            assert len(found) == 1
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_find_procedures_referencing_claim_returns_empty_for_unreferenced_claim():
    """The common case: a claim_id nothing points at returns [] with no
    error, not a failure."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            found = await find_procedures_referencing_claim(pool, str(uuid.uuid4()))
            assert found == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_propagate_claim_change_marks_referencing_procedure_stale():
    """The real end-to-end proof: propagate_claim_change finds the
    referencing procedure and actually flips its staleness column to
    'stale' via the real mark_procedure_stale -- verified by re-reading
    the row from the database, not by trusting the return value alone."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            target_claim_id = str(uuid.uuid4())

            referencing = await capture_procedure(
                pool, name=f"{PREFIX}-propagate", goal="g",
                preconditions=[
                    precondition_with_claim(
                        "repo", "has_version", "2.0", claim_id=target_claim_id,
                    ),
                ],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = referencing["id"]

            before = await pool.fetchrow("SELECT staleness::text FROM procedures WHERE id = $1", row_id)
            assert before["staleness"] == "fresh"

            processed = await propagate_claim_change(
                pool, target_claim_id,
                reason="claim superseded by a fresher measurement",
                detected_by="test-claim-impact",
            )
            # propagate_claim_change now returns a dict (B5 role-aware
            # invalidation): strong-role refs are marked stale, explanatory
            # refs are returned untouched. This precondition claim ref is a
            # strong role, so it lands in marked_stale.
            assert processed["marked_stale"] == [row_id]
            assert processed["explanatory_untouched"] == []

            after = await pool.fetchrow("SELECT staleness::text FROM procedures WHERE id = $1", row_id)
            assert after["staleness"] == "stale"

            change_set = await pool.fetchrow(
                "SELECT author, reason FROM change_sets WHERE author = $1 ORDER BY created_at DESC LIMIT 1",
                "test-claim-impact",
            )
            assert change_set is not None
            assert "claim superseded" in change_set["reason"]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_propagate_claim_change_is_a_noop_for_unreferenced_claim():
    """Zero affected procedures is success, not an error -- the common
    case for most claims."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            processed = await propagate_claim_change(
                pool, str(uuid.uuid4()),
                reason="irrelevant", detected_by="test-claim-impact",
            )
            assert processed["marked_stale"] == []
            assert processed["explanatory_untouched"] == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
