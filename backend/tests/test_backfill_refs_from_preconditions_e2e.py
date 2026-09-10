"""
A33 -- proving `procedure_claim_refs.backfill_refs_from_preconditions`
against a real Postgres: the out-of-band, idempotent corpus backfill that
lifts legacy `procedures.preconditions[*].claim_id` into the typed
`procedure_claim_refs` relation with `role='PRECONDITION'`,
`ref_origin='backfilled'`.

This seeds its own representative rows (one procedure with two
claim-bearing preconditions + one claim-less precondition, one procedure
with none) and asserts the backfill's real effect + idempotency. The real
once-per-environment run against the production corpus is a deploy step.

CAVEAT the test also documents: the backfill scans
`ORDER BY t_created ASC LIMIT n`; a corpus larger than `n` needs a `limit`
>= its size or repeated runs (the newest rows sort last).

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.procedure_claim_refs import backfill_refs_from_preconditions
from app.services.procedure_extraction.derive import precondition_with_claim
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database backfill test"
)

_PREFIX = "backfill-a33-e2e"


async def _cleanup(pool) -> None:
    ids = [
        r["procedure_id"]
        for r in await pool.fetch(
            "SELECT DISTINCT procedure_id FROM procedures WHERE name LIKE $1", f"{_PREFIX}%"
        )
    ]
    for pid in ids:
        await pool.execute("DELETE FROM procedure_claim_refs WHERE procedure_id = $1", pid)
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{_PREFIX}%")


def test_backfill_lifts_precondition_claim_ids_and_is_idempotent():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            claim_id = str(uuid.uuid4())
            other_claim_id = str(uuid.uuid4())

            with_claim = await capture_procedure(
                pool, name=f"{_PREFIX}-with-claim", goal="g",
                preconditions=[
                    precondition_with_claim("repo", "has_version", "2.0", claim_id=claim_id),
                    precondition_with_claim("py", "version_gte", "3.11", claim_id=other_claim_id),
                    # a precondition with NO claim_id -- must be ignored by the backfill
                    {"subject": "os", "predicate": "is", "object": "linux"},
                ],
                provenance="system_pending_review", scope_type="global",
            )
            no_claim = await capture_procedure(
                pool, name=f"{_PREFIX}-no-claim", goal="g",
                preconditions=[{"subject": "repo", "predicate": "has_version", "object": "1.0"}],
                provenance="system_pending_review", scope_type="global",
            )

            # --- first run: creates exactly the two claim-bearing refs ---
            # NOTE: the backfill scans `ORDER BY t_created ASC LIMIT n`, so a
            # corpus larger than `n` needs a `limit` >= its size (or repeated
            # runs). The freshly-seeded rows are the NEWEST -> pass a limit
            # well above any test DB's live-procedure count.
            r1 = await backfill_refs_from_preconditions(pool, limit=1_000_000)
            assert r1["procedures_scanned"] >= 2  # at least our two
            assert r1["refs_created"] >= 2

            refs = await pool.fetch(
                "SELECT claim_id::text AS claim_id, role, ref_origin, procedure_version "
                "FROM procedure_claim_refs "
                "WHERE procedure_id = $1 AND t_invalid IS NULL ORDER BY claim_id",
                with_claim["procedure_id"],
            )
            got = {(r["claim_id"], r["role"], r["ref_origin"]) for r in refs}
            assert (claim_id, "PRECONDITION", "backfilled") in got
            assert (other_claim_id, "PRECONDITION", "backfilled") in got
            assert len(refs) == 2, "the claim-less precondition must NOT produce a ref"
            assert all(r["procedure_version"] == 1 for r in refs), "fresh capture is version 1"

            # the procedure with no claim-bearing preconditions gets nothing
            none_for_no_claim = await pool.fetchval(
                "SELECT count(*) FROM procedure_claim_refs WHERE procedure_id = $1",
                no_claim["procedure_id"],
            )
            assert none_for_no_claim == 0

            # --- second run: idempotent -- nothing new, all already-present ---
            r2 = await backfill_refs_from_preconditions(pool, limit=1_000_000)
            assert r2["refs_created"] == 0
            assert r2["already_present"] >= 2

            still = await pool.fetchval(
                "SELECT count(*) FROM procedure_claim_refs "
                "WHERE procedure_id = $1 AND t_invalid IS NULL",
                with_claim["procedure_id"],
            )
            assert still == 2, "re-running the backfill must not duplicate refs"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
