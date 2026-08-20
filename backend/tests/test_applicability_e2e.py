"""
Real, live-database tests for applicability.py. Same pattern as the
other e2e test files: requires a real DATABASE_URL, skips (not fails)
without one.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.applicability import (
    MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL,
    check_hard_constraints,
    find_applicable_procedures,
    should_disable_procedure_retrieval,
)
from app.services.claims import capture_claim
from app.services.procedures import capture_procedure, record_execution_outcome

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.25] * 1024


async def _cleanup(pool, name_prefix: str, subject_prefix: str = "") -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")
    if subject_prefix:
        await pool.execute(
            "DELETE FROM knowledge_nodes WHERE node_type = 'claim' "
            "AND properties->>'subject' LIKE $1", f"{subject_prefix}%",
        )
        await pool.execute(
            "DELETE FROM task_nodes WHERE skill_ref LIKE $1", f"{subject_prefix}%"
        )


async def _make_verified_procedure(pool, name: str, **kwargs) -> str:
    """Real helper: drives a fresh procedure to `verified` the same way
    the real lifecycle does (10 successes, 0 failures, 3+ contexts) --
    not by writing the enum value directly, so these tests exercise the
    real path a verified procedure actually took to get there."""
    from app.services.procedures import MIN_DISTINCT_CONTEXTS_FOR_VERIFIED, MIN_SUCCESSES_FOR_VERIFIED

    result = await capture_procedure(pool, name=name, goal="g", **kwargs)
    row_id = result["id"]
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    # REAL, deliberate addition (migration 20): statistical verification
    # and human approval are two orthogonal gates now -- applicability.py
    # requires BOTH under require_verified=True (ticket 13's rule
    # extended consistently: "verified gates automatic retrieval"
    # applies to approval too). This fixture name says "verified", and
    # every test using it is exercising OTHER hard constraints (scope,
    # preconditions, exclusions) -- so it must also be approved, or every
    # one of those tests would fail on approval_status instead of the
    # thing they're actually testing.
    await pool.execute(
        "UPDATE procedures SET approval_status = 'approved' WHERE id = $1::uuid", row_id,
    )
    return row_id


def test_cold_start_disables_retrieval_when_no_verified_procedures_exist():
    """Ticket 12's cold-start answer, confirmed directly: with zero
    verified procedures, retrieval must be disabled entirely -- not
    degraded to unverified candidates, not similarity-only.

    Real isolation discipline: rather than a blunt global UPDATE that
    could corrupt other tests' data, this snapshots which procedures are
    currently verified, temporarily retires exactly those, and restores
    them in `finally` -- safe regardless of what else exists in the
    database or what order tests run in.
    """
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        previously_verified_ids: list = []
        try:
            previously_verified_ids = [
                r["id"] for r in await pool.fetch(
                    "SELECT id FROM procedures WHERE verification_state = 'verified'"
                )
            ]
            if previously_verified_ids:
                await pool.execute(
                    "UPDATE procedures SET verification_state = 'retired' WHERE id = ANY($1::uuid[])",
                    previously_verified_ids,
                )

            disabled = await should_disable_procedure_retrieval(pool)
            assert disabled is True

            results = await find_applicable_procedures(pool, current_scope={})
            assert results == [], "find_applicable_procedures must return [] during cold start, not raise or degrade"
        finally:
            if previously_verified_ids:
                await pool.execute(
                    "UPDATE procedures SET verification_state = 'verified' WHERE id = ANY($1::uuid[])",
                    previously_verified_ids,
                )
            await pool.close()

    asyncio.run(_run())


def test_retrieval_enables_once_threshold_verified_procedures_exist():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-coldstart-enable")
            for i in range(MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL):
                await _make_verified_procedure(pool, f"app-test-coldstart-enable-{i}")

            disabled = await should_disable_procedure_retrieval(pool)
            assert disabled is False
        finally:
            await _cleanup(pool, "app-test-coldstart-enable")
            await pool.close()

    asyncio.run(_run())


def test_missing_precondition_fails_closed_not_open():
    """The core CWA decision, confirmed directly: a precondition with no
    matching claim in the graph must be UNSATISFIED, not treated as
    unknown-and-therefore-passable."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-failclosed", subject_prefix="app-test-failclosed")
            row_id = await _make_verified_procedure(
                pool, "app-test-failclosed-1",
                preconditions=[{"subject": "app-test-failclosed-subject", "predicate": "status", "object": "ready"}],
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            result = await check_hard_constraints(pool, dict(row))
            assert result.applicable is False
            assert any("precondition" in c for c in result.failed_constraints)
        finally:
            await _cleanup(pool, "app-test-failclosed", subject_prefix="app-test-failclosed")
            await pool.close()

    asyncio.run(_run())


def test_satisfied_precondition_passes():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-satisfied", subject_prefix="app-test-satisfied")
            subject = "app-test-satisfied-subject"
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", subject
            )
            await capture_claim(
                pool, statement="ready", task_ids=[subject], subject=subject,
                predicate="status", object="ready", embedder=FakeEmbedder(),
            )
            row_id = await _make_verified_procedure(
                pool, "app-test-satisfied-1",
                preconditions=[{"subject": subject, "predicate": "status", "object": "ready"}],
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            result = await check_hard_constraints(pool, dict(row))
            assert result.applicable is True
        finally:
            await _cleanup(pool, "app-test-satisfied", subject_prefix="app-test-satisfied")
            await pool.close()

    asyncio.run(_run())


def test_wrong_object_value_fails_the_precondition():
    """Not just presence-of-claim -- the claim's actual object value
    must match, or it's still unsatisfied."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-wrongval", subject_prefix="app-test-wrongval")
            subject = "app-test-wrongval-subject"
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", subject
            )
            await capture_claim(
                pool, statement="not ready", task_ids=[subject], subject=subject,
                predicate="status", object="not_ready", embedder=FakeEmbedder(),
            )
            row_id = await _make_verified_procedure(
                pool, "app-test-wrongval-1",
                preconditions=[{"subject": subject, "predicate": "status", "object": "ready"}],
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            result = await check_hard_constraints(pool, dict(row))
            assert result.applicable is False
        finally:
            await _cleanup(pool, "app-test-wrongval", subject_prefix="app-test-wrongval")
            await pool.close()

    asyncio.run(_run())


def test_high_similarity_cannot_compensate_for_a_violated_precondition():
    """THE core non-compensatory guarantee, tested directly against the
    exact failure mode ticket 12 names (criterion compensation): a
    procedure that would rank #1 by similarity but has an unsatisfied
    precondition must be excluded entirely from find_applicable_procedures(),
    not merely ranked lower."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-noncompensatory", subject_prefix="app-test-noncompensatory")
            # A procedure identical in every way except it has an
            # impossible-to-satisfy precondition.
            blocked_id = await _make_verified_procedure(
                pool, "app-test-noncompensatory-blocked",
                preconditions=[{"subject": "app-test-noncompensatory-subject", "predicate": "x", "object": "y"}],
                embedding=[0.9] * 1024,  # deliberately near-identical to the query embedding
            )
            allowed_id = await _make_verified_procedure(
                pool, "app-test-noncompensatory-allowed",
                embedding=[0.1] * 1024,  # deliberately far from the query embedding
            )

            results = await find_applicable_procedures(
                pool, goal_embedding=[0.9] * 1024, current_scope={},
            )
            result_ids = {str(r["id"]) for r in results}
            assert blocked_id not in result_ids, (
                "a procedure with a violated precondition must never appear, "
                "regardless of how high its similarity score would be"
            )
            assert allowed_id in result_ids
        finally:
            await _cleanup(pool, "app-test-noncompensatory", subject_prefix="app-test-noncompensatory")
            await pool.close()

    asyncio.run(_run())


def test_candidate_procedure_excluded_from_automatic_selection_but_explicitly_invocable():
    """Ticket 13's exact wording, tested both halves: 'verified gates
    automatic retrieval; a candidate procedure remains explicitly
    invocable.'"""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-candidate")
            result = await capture_procedure(pool, name="app-test-candidate-1", goal="g")
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"])

            automatic = await check_hard_constraints(pool, dict(row), require_verified=True)
            assert automatic.applicable is False
            assert "verification_state" in automatic.failed_constraints

            explicit = await check_hard_constraints(pool, dict(row), require_verified=False)
            assert explicit.applicable is True
        finally:
            await _cleanup(pool, "app-test-candidate")
            await pool.close()

    asyncio.run(_run())


def test_verified_but_unapproved_procedure_excluded_from_automatic_selection():
    """
    REAL GAP CLOSED, tested directly: migration 20 added approval_status
    as a column, but nothing checked it until this session's second
    real caller of find_applicable_procedures() was being wired -- a
    procedure could reach 'verified' via pure statistics (10 successes,
    0 failures, 3+ contexts) with a human never having approved it, and
    automatic retrieval would have silently surfaced it anyway. Same
    require_verified split as candidate-vs-verified: explicit invocation
    still bypasses this, matching ticket 13's rule extended consistently
    from verification to approval.
    """
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-unapproved")
            from app.services.procedures import MIN_DISTINCT_CONTEXTS_FOR_VERIFIED, MIN_SUCCESSES_FOR_VERIFIED
            result = await capture_procedure(pool, name="app-test-unapproved-1", goal="g")
            row_id = result["id"]
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            # Deliberately NOT approved -- this is the whole point of the test.
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["verification_state"] == "verified", "fixture must actually reach verified"
            assert row["approval_status"] == "proposed", "fixture must stay unapproved"

            automatic = await check_hard_constraints(pool, dict(row), require_verified=True)
            assert automatic.applicable is False
            assert "approval_status" in automatic.failed_constraints

            explicit = await check_hard_constraints(pool, dict(row), require_verified=False)
            assert explicit.applicable is True

            results = await find_applicable_procedures(pool, current_scope={}, require_verified=True)
            assert row_id not in {r["id"] for r in results}, (
                "a verified-but-unapproved procedure must never be automatically selected"
            )
        finally:
            await _cleanup(pool, "app-test-unapproved")
            await pool.close()

    asyncio.run(_run())


def test_quarantined_procedure_is_never_applicable():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-quarantine")
            result = await capture_procedure(pool, name="app-test-quarantine-1", goal="g")
            row_id = result["id"]
            for i in range(5):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=False, context_key=f"ctx-{i}",
                )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["availability"] == "quarantined"

            check = await check_hard_constraints(pool, dict(row), require_verified=False)
            assert check.applicable is False
            assert "availability" in check.failed_constraints
        finally:
            await _cleanup(pool, "app-test-quarantine")
            await pool.close()

    asyncio.run(_run())


def test_stale_procedure_is_never_applicable_even_if_verified():
    """Ticket 13's exact concern: 'a stale procedure is reused as though
    verified' -- staleness must disqualify independently of
    verification_state."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-stale")
            row_id = await _make_verified_procedure(pool, "app-test-stale-1")
            await pool.execute("UPDATE procedures SET staleness = 'stale' WHERE id = $1", row_id)
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["verification_state"] == "verified"

            result = await check_hard_constraints(pool, dict(row))
            assert result.applicable is False
            assert "staleness" in result.failed_constraints
        finally:
            await _cleanup(pool, "app-test-stale")
            await pool.close()

    asyncio.run(_run())


def test_scope_mismatch_excludes_procedure():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-scope")
            row_id = await _make_verified_procedure(
                pool, "app-test-scope-1", scope={"repo": ["backend"]},
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)

            wrong_scope = await check_hard_constraints(
                pool, dict(row), current_scope={"repo": ["frontend"]},
            )
            assert wrong_scope.applicable is False
            assert "scope" in wrong_scope.failed_constraints

            right_scope = await check_hard_constraints(
                pool, dict(row), current_scope={"repo": ["backend"]},
            )
            assert right_scope.applicable is True
        finally:
            await _cleanup(pool, "app-test-scope")
            await pool.close()

    asyncio.run(_run())


def test_exclusion_match_excludes_procedure_even_with_matching_scope():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-exclusion")
            row_id = await _make_verified_procedure(
                pool, "app-test-exclusion-1",
                exclusions=[{"key": "file_pattern", "values": ["*.generated.py"]}],
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)

            excluded = await check_hard_constraints(
                pool, dict(row), current_scope={"file_pattern": ["*.generated.py"]},
            )
            assert excluded.applicable is False
            assert "exclusions" in excluded.failed_constraints
        finally:
            await _cleanup(pool, "app-test-exclusion")
            await pool.close()

    asyncio.run(_run())


def test_expired_procedure_is_not_applicable():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-expired")
            row_id = await _make_verified_procedure(pool, "app-test-expired-1")
            await pool.execute(
                "UPDATE procedures SET t_invalid = now() - interval '1 day' WHERE id = $1", row_id
            )
            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            result = await check_hard_constraints(pool, dict(row))
            assert result.applicable is False
            assert "temporal_validity" in result.failed_constraints
        finally:
            await _cleanup(pool, "app-test-expired")
            await pool.close()

    asyncio.run(_run())


def test_no_embedding_returns_unranked_survivors_not_an_error():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "app-test-noembed")
            await _make_verified_procedure(pool, "app-test-noembed-1")

            results = await find_applicable_procedures(pool, current_scope={}, goal_embedding=None)
            assert any(r["name"] == "app-test-noembed-1" for r in results)
        finally:
            await _cleanup(pool, "app-test-noembed")
            await pool.close()

    asyncio.run(_run())
