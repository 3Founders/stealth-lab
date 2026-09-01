"""
Full-loop live proof of the Ideal V1 staleness/claim-impact directive:
"procedure works in environment A -> relevant assumption changes ->
procedure becomes stale/inapplicable -> system refuses/reranks it ->
better applicable procedure can win. The user should be able to see
why."

Every piece this test exercises already exists as a real, independently
tested production primitive (see test_claim_impact_e2e.py,
test_precondition_claim_provenance_e2e.py): a claim-tied precondition
(`procedure_extraction/derive.py::precondition_with_claim`), claims.py's
`relate_claims()` (which flips truth_state and, by default, calls
`claim_impact.propagate_claim_change()`), `procedures.py::
mark_procedure_stale()`, applicability.py's `find_applicable_procedures()`
cascade, and `check_procedure_reuse()`'s human-readable verdict. What was
NOT proven anywhere in the suite before this file is that all of them
compose end to end: a real claim change actually changes which procedure
a real retrieval call returns, and the reason is real and inspectable --
not five isolated unit proofs of the pieces.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.applicability import check_procedure_reuse, find_applicable_procedures
from app.services.claims import capture_claim, relate_claims
from app.services.procedure_extraction.derive import precondition_with_claim
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "staleness-selection-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


def test_claim_change_propagates_to_real_retrieval_selection_with_inspectable_reason():
    """The full loop, real code path throughout, no manual state pokes
    except cleanup DELETEs:

    1. A real claim (X) backs a real precondition on procedure A.
    2. Both A (claim-gated) and B (no preconditions, always applicable)
       are returned by a real find_applicable_procedures() call.
    3. A real newer claim (Y) SUPERSEDES X via relate_claims() -- the
       actual production entry point for a belief change, not
       propagate_claim_change() called directly.
    4. (a) A's `staleness` column actually flips to 'stale' in the DB,
       via the real mark_procedure_stale() call relate_claims() triggers
       -- proven by re-reading the row, not by trusting a return value.
    5. (b) A second real find_applicable_procedures() call now excludes
       A and still returns B -- the "better applicable procedure wins"
       requirement, decided entirely by production code.
    6. (c) check_procedure_reuse() on A explicitly names 'stale' in a
       human-readable reason -- the "user can see why" requirement.
    """
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            tag = uuid.uuid4().hex[:8]
            task_name = f"{PREFIX}-task-{tag}"
            subject = f"{PREFIX}:repo:{tag}"

            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
                task_name, f"skill_{task_name}",
            )

            claim_x = await capture_claim(
                pool,
                statement=f"{PREFIX} {subject} has_version 2.0",
                task_ids=[f"skill_{task_name}"],
                subject=subject, predicate="has_version", object="2.0",
                embedder=FakeEmbedder(),
            )
            assert claim_x

            procedure_a = await capture_procedure(
                pool, name=f"{PREFIX}-A-claim-gated", goal=f"{PREFIX} goal",
                preconditions=[
                    precondition_with_claim(subject, "has_version", "2.0", claim_id=claim_x),
                ],
                provenance="system_pending_review", scope_type="global",
            )
            procedure_b = await capture_procedure(
                pool, name=f"{PREFIX}-B-alternative", goal=f"{PREFIX} goal",
                preconditions=[],
                provenance="system_pending_review", scope_type="global",
            )

            # --- Before: both are real, applicable candidates. ---
            # candidate_pool_size raised above the default 200: this is a
            # shared live database with hundreds of residual procedures
            # from prior test runs, and the cost-only pre-filter (no
            # goal_embedding here) orders zero-precondition candidates
            # first -- a real accumulation effect of a shared DB, not a
            # production wiring gap, so the test widens the pool rather
            # than papering over it with cleanup this file doesn't own.
            before = await find_applicable_procedures(
                pool, goal_embedding=None, current_scope={}, access_scope=None,
                require_verified=False, limit=2000, candidate_pool_size=2000,
            )
            before_ids = {str(row["id"]) for row in before}
            assert procedure_a["id"] in before_ids
            assert procedure_b["id"] in before_ids

            before_staleness = await pool.fetchval(
                "SELECT staleness::text FROM procedures WHERE id = $1", procedure_a["id"],
            )
            assert before_staleness == "fresh"

            # --- The real change: a fresher claim supersedes claim_x via
            # the actual production entry point (relate_claims), which by
            # default propagates impact to any procedure whose
            # precondition names claim_x. ---
            claim_y = await capture_claim(
                pool,
                statement=f"{PREFIX} {subject} has_version 3.0",
                task_ids=[f"skill_{task_name}"],
                subject=subject, predicate="has_version", object="3.0",
                embedder=FakeEmbedder(),
            )
            assert claim_y

            stale_marked = await relate_claims(
                pool, from_claim_id=claim_y, to_claim_id=claim_x, relation="SUPERSEDES",
            )
            assert procedure_a["id"] in stale_marked

            # --- (a): staleness actually changed, via the real column. ---
            after_staleness = await pool.fetchval(
                "SELECT staleness::text FROM procedures WHERE id = $1", procedure_a["id"],
            )
            assert after_staleness == "stale"

            # --- (b): a real retrieval call now excludes A; B still wins. ---
            after = await find_applicable_procedures(
                pool, goal_embedding=None, current_scope={}, access_scope=None,
                require_verified=False, limit=2000, candidate_pool_size=2000,
            )
            after_ids = {str(row["id"]) for row in after}
            assert procedure_a["id"] not in after_ids
            assert procedure_b["id"] in after_ids

            # --- (c): the reason is real and inspectable. ---
            verdict = await check_procedure_reuse(
                pool, procedure_id=procedure_a["procedure_id"],
                current_scope={}, access_scope=None,
            )
            assert verdict.verdict == "WOULD_REFUSE"
            assert "stale" in verdict.reason
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
