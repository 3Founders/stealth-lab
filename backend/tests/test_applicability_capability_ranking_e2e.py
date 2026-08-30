"""
Live, real-database proving test for Phase 3 of the trust-pipeline plan
(plan §Phase 3 -- procedure_extraction/capability.py wiring): capability
score, computed via the SAME real Wilson-lower-bound math and the SAME
capability_for_stream() recompute check_procedure_reuse's capability_note
already uses, now actually influences find_applicable_procedures()'s
final candidate order.

Founder's own G2 example, made real: a lower-similarity, higher-
capability applicable procedure must outrank a higher-similarity,
weak-capability one. Before this pass, capability.py had ZERO real
callers -- applicability.py ranked survivors by semantic similarity
ALONE. This test constructs three real applicable procedures --

  * WEAK  -- highest semantic similarity to the goal, ZERO recorded
    execution evidence (capability p_estimate == 0.0, the honest
    "no evidence" floor).
  * STRONG -- second-highest similarity (deliberately a little behind
    WEAK, not the worst), with 10 REAL recorded successes across 3
    distinct contexts via app.services.procedures.record_execution_
    outcome -- the real evidence writer, not a fabricated stats blob.
  * DISTRACTOR -- lowest similarity, ONE real recorded success (weak
    but nonzero capability) -- exists only to break the otherwise
    perfectly symmetric 2-item RRF tie a WEAK/STRONG-only pool would
    produce (Reciprocal Rank Fusion of two rank-reversed 2-item lists
    always ties exactly; a third, differently-ordered item is required
    to observe the real preference this test is proving).

-- and asserts STRONG is ranked ahead of WEAK in find_applicable_
procedures()'s real output, against real Postgres, through the real
non-compensatory cascade (require_verified=False, ticket 13's own named
"explicit invocation" exception -- the same choice check_procedure_reuse
makes; this test is proving the RANKING step, not the verification-
threshold gate, so it deliberately doesn't entangle the two).

Skips (not fails) without a real DATABASE_URL -- same pattern as every
other *_e2e.py file in this suite.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
from app.services.procedures import capture_procedure, record_execution_outcome

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

_DIM = 1024


def _unit_vec(index: int) -> list[float]:
    v = [0.0] * _DIM
    v[index] = 1.0
    return v


# Query points straight along axis 0. WEAK's embedding is IDENTICAL to
# the query (cosine similarity 1.0, the best possible match). STRONG is
# a small step away from the query (still close, but strictly behind
# WEAK). DISTRACTOR is orthogonal to the query (cosine similarity 0.0,
# the worst match) -- real separation, not a hand-tuned edge case.
QUERY_VEC = _unit_vec(0)
WEAK_VEC = _unit_vec(0)


def _strong_vec() -> list[float]:
    v = [0.0] * _DIM
    v[0] = 0.9
    v[1] = 0.1
    return v


def _distractor_vec() -> list[float]:
    return _unit_vec(1)


async def _cleanup(pool, name_prefix: str) -> None:
    # evidence is append-only by engine trigger (Band 1.9a, invariant
    # #19 -- "nothing is deleted", CLAUDE.md's bi-temporal rule):
    # DELETE is rejected outright, so this test does not attempt to
    # clean up evidence rows it wrote. That's honest, not a leak -- a
    # fresh run generates fresh uuid7 procedure ids, so orphaned
    # evidence from a prior run's now-deleted procedure rows can never
    # collide with or be matched by this run's queries (target_id won't
    # exist in `procedures` any more, and nothing else joins evidence to
    # procedures by name).
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_strong_capability_outranks_weak_capability_at_equal_applicability():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "app-test-capability-rank"
        try:
            await _cleanup(pool, prefix)

            weak = await capture_procedure(
                pool, name=f"{prefix}-weak", goal="deploy service to prod",
                provenance="system_pending_review", scope_type="global",
                embedding=WEAK_VEC,
            )
            strong = await capture_procedure(
                pool, name=f"{prefix}-strong", goal="deploy microservice",
                provenance="system_pending_review", scope_type="global",
                embedding=_strong_vec(),
            )
            distractor = await capture_procedure(
                pool, name=f"{prefix}-distractor", goal="unrelated task",
                provenance="system_pending_review", scope_type="global",
                embedding=_distractor_vec(),
            )

            # WEAK: zero recorded evidence -- the honest "no evidence yet"
            # floor, p_estimate == 0.0.

            # STRONG: 10 REAL successes across 3 distinct contexts, via
            # the real evidence writer (WAVE-3's record_execution_outcome,
            # app/execution/evidence.py::outcome_to_evidence under it) --
            # not a fabricated verification_stats blob.
            for i in range(10):
                await record_execution_outcome(
                    pool, procedure_row_id=strong["id"], success=True,
                    context_key=f"ctx-{i % 4}",
                )

            # DISTRACTOR: one real success -- weak but nonzero capability,
            # strictly between WEAK (0.0) and STRONG. Exists only to break
            # the RRF tie a 2-item WEAK/STRONG-only pool would produce
            # (see module docstring).
            await record_execution_outcome(
                pool, procedure_row_id=distractor["id"], success=True,
                context_key="ctx-0",
            )

            results = await find_applicable_procedures(
                pool, goal_embedding=QUERY_VEC, current_scope={},
                require_verified=False, limit=10,
            )
            names = [r["name"] for r in results]
            assert f"{prefix}-weak" in names and f"{prefix}-strong" in names, (
                f"both candidates must be real, hard-filter-passing survivors: {names}"
            )

            weak_rank = names.index(f"{prefix}-weak")
            strong_rank = names.index(f"{prefix}-strong")
            assert strong_rank < weak_rank, (
                "founder's G2 example: a lower-similarity, higher-capability "
                "applicable procedure must outrank a higher-similarity, "
                f"weak-capability one -- got order {names}"
            )

            # Sanity check on the setup itself: WEAK really is the closer
            # embedding match (its own similarity score, when present,
            # must exceed STRONG's) -- so the reordering above is provably
            # capability's doing, not an accidental similarity tie.
            by_name = {r["name"]: r for r in results}
            weak_sim = by_name[f"{prefix}-weak"].get("_similarity_score")
            strong_sim = by_name[f"{prefix}-strong"].get("_similarity_score")
            assert weak_sim is not None and strong_sim is not None
            assert weak_sim > strong_sim, (
                "sanity check on the fixture: WEAK must be the more semantically "
                f"similar candidate (weak={weak_sim}, strong={strong_sim}) -- "
                "otherwise this test doesn't actually exercise capability ranking"
            )
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
