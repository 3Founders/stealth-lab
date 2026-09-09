"""Phase 5: concurrency/chaos, small scale (spec section 28-29).

Per an explicit user scope decision: correctness-first at small/modest
scale (tens-to-low-hundreds of concurrent operations), proving the
named safety invariants -- no duplicate evidence, no double counting,
no lost updates, no trust inflation, no version corruption -- not
capacity-at-scale (no 1k->1M testing; that is spec section 30, out of
scope this pass).

Same real-pool, real-asyncio-concurrency, skip-not-fail-without-
DATABASE_URL convention as tests/test_load_e2e.py, which this file sits
beside rather than duplicates: that file already proves raw concurrent
THROUGHPUT correctness (N distinct writes, N distinct rows, no scope
leak under mixed read/write). This file targets the specific race
SHAPES spec section 28 names that test_load_e2e.py doesn't cover:
same-row contention (not just distinct rows), identical-key
resubmission (not just distinct keys), a live claim-supersession race,
and fault injection.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from app.db.session import create_pool
from app.services.applicability import check_hard_constraints
from app.services.claims import capture_claim
from app.services.procedures import capture_procedure, record_execution_outcome

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "concurrency-chaos-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.3] * 1024


class RaisingEmbedder:
    """Fault injection: simulates an embedding-provider timeout. Used to
    prove capture_claim() fails atomically -- no partial row -- rather
    than silently swallowing the failure into a false success."""

    async def embed_one(self, text, input_type="document"):
        raise asyncio.TimeoutError("simulated embedding provider timeout")


async def _cleanup(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE skill_ref LIKE $1", f"{PREFIX}%")


def _run(coro):
    return asyncio.run(coro)


# --- 1. duplicate job processing / resumed execution: same-row contention ---

def test_concurrent_execution_outcomes_on_distinct_contexts_lose_none_under_the_row_lock():
    """N concurrent record_execution_outcome() calls on the SAME
    procedure row, each a genuinely distinct context. record_execution_
    outcome() takes `SELECT ... FOR UPDATE` on the procedure row before
    mutating verification_stats (procedures.py's own documented
    discipline) -- this is the actual mechanism under test: does the
    row lock really serialize concurrent writers, or does asyncpg/
    Postgres let one clobber another's read-modify-write under real
    concurrency (not just in the single-writer case test_load_e2e.py
    already covers elsewhere)? Correctness bar: NO lost update -- final
    attempts == N and distinct_contexts == N, exactly, not less."""
    N = 60

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-row-lock-target", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            results = await asyncio.gather(*[
                record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"{PREFIX}-ctx-{i}",
                )
                for i in range(N)
            ], return_exceptions=True)

            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"{len(errors)}/{N} concurrent outcome writes raised: {errors[:3]}"

            final = await pool.fetchrow(
                "SELECT verification_stats FROM procedures WHERE id = $1", row_id
            )
            stats = dict(final["verification_stats"])
            assert stats["attempts"] == N, (
                f"lost update under concurrency: expected {N} attempts, got {stats['attempts']}"
            )
            assert stats["distinct_contexts"] == N, (
                f"lost update under concurrency: expected {N} distinct contexts, "
                f"got {stats['distinct_contexts']}"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


def test_concurrent_identical_context_key_resubmission_does_not_inflate_distinct_contexts():
    """The trust-inflation case: M concurrent record_execution_outcome()
    calls all citing the SAME context_key (e.g. the same execution
    resumed/retried and its outcome double-submitted by a racing
    caller). Every attempt is still individually recorded (attempts ==
    M -- this function does not deduplicate attempts, only contexts),
    but distinct_contexts -- the signal capability.py's independence-
    group logic ultimately leans on -- must stay 1, never M. A race that
    let this inflate would be a real trust-inflation bug: the same
    context masquerading as M independent ones."""
    M = 25

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-dup-context-target", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            results = await asyncio.gather(*[
                record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"{PREFIX}-same-ctx",
                )
                for _ in range(M)
            ], return_exceptions=True)

            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"{len(errors)}/{M} concurrent outcome writes raised: {errors[:3]}"

            final = await pool.fetchrow(
                "SELECT verification_stats FROM procedures WHERE id = $1", row_id
            )
            stats = dict(final["verification_stats"])
            assert stats["attempts"] == M, f"expected {M} attempts recorded, got {stats['attempts']}"
            assert stats["distinct_contexts"] == 1, (
                f"TRUST INFLATION: {M} concurrent submissions of the SAME context_key produced "
                f"distinct_contexts={stats['distinct_contexts']}, expected 1"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


# --- 2. simultaneous publishers ---

def test_concurrent_candidate_publishes_by_simulated_users_land_as_distinct_uncorrupted_rows():
    """Two (simulated) users concurrently publish what would look like
    'the same' procedure (identical name/goal) -- e.g. two agents both
    independently discovering and capturing the same fix at once.
    capture_procedure() has no dedup-on-insert (merge_duplicate_
    procedures() is a separate, explicit sweep, not automatic -- see
    procedures.py) so the correct, honest behavior is: both land as
    distinct, individually well-formed candidate rows, no corruption,
    no silent merge, no lost write. This test proves that positively
    rather than assuming it."""
    N = 15

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool)

            results = await asyncio.gather(*[
                capture_procedure(
                    pool, name=f"{PREFIX}-simultaneous-publish", goal="same goal, N publishers",
                    provenance="system_pending_review", scope_type="global",
                )
                for _ in range(N)
            ], return_exceptions=True)

            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"{len(errors)}/{N} concurrent publishes raised: {errors[:3]}"

            ids = {r["id"] for r in results}
            assert len(ids) == N, f"expected {N} distinct procedure ids, got {len(ids)} -- collision under concurrency"

            rows = await pool.fetch(
                "SELECT id, verification_state, availability, staleness FROM procedures "
                "WHERE id = ANY($1::uuid[])", list(ids),
            )
            assert len(rows) == N, f"expected {N} procedure rows, found {len(rows)} -- a write was lost"
            for row in rows:
                assert row["verification_state"] == "candidate", (
                    "a concurrently-published procedure did not land in the correct starting state"
                )
                assert row["availability"] == "active"
                assert row["staleness"] == "fresh"
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


# --- 3. claim-update-during-retrieval ---

def test_claim_supersession_racing_applicability_reads_never_produces_a_torn_result():
    """One writer supersedes a claim (the real belief-change path, via
    supersede_claim -> relate_claims(SUPERSEDES) flipping the prior
    claim's truth_state to OUT) while K readers concurrently call the
    real check_hard_constraints() against a procedure whose precondition
    is bound to that specific claim_id.

    Correctness bar per spec section 28: no torn read. Every single read
    must resolve to a well-formed, self-consistent applicability result
    -- either the pre-supersession state (claim still IN, procedure
    applicable) or the post-supersession state (claim OUT, procedure
    disqualified) -- never an exception, and never a result that is
    internally inconsistent (e.g. applicable=True with a populated
    failed_constraints list, or vice versa). This does not assert WHICH
    side of the race each individual read lands on -- that is
    legitimately non-deterministic -- only that every landing is valid.
    """
    K_READS = 40

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                f"{PREFIX}-claim-race-task", f"{PREFIX}-claim-race-skill",
            )
            subject = f"{PREFIX}:claim-race-subject"
            embedder = FakeEmbedder()

            claim_id = await capture_claim(
                pool, statement=f"{PREFIX} {subject} language python",
                task_ids=[f"{PREFIX}-claim-race-skill"],
                subject=subject, predicate="language", object="python",
                embedder=embedder,
            )
            assert claim_id

            procedure = {
                "id": "00000000-0000-4000-8000-000000000042",
                "t_invalid": None,
                "staleness": "fresh",
                "availability": "active",
                "verification_state": "verified",
                "approval_status": "approved",
                "scope": {},
                "exclusions": [],
                "preconditions": [
                    {"subject": subject, "predicate": "language", "object": "python", "claim_id": claim_id},
                ],
                "invariants": [],
            }

            from app.services.claims import supersede_claim

            async def _read():
                return await check_hard_constraints(pool, dict(procedure))

            async def _write():
                return await supersede_claim(
                    pool, prior_claim_id=claim_id,
                    statement=f"{PREFIX} {subject} language rust (superseded)",
                    task_ids=[f"{PREFIX}-claim-race-skill"],
                    subject=subject, predicate="language", object="rust",
                    embedder=embedder,
                )

            results = await asyncio.gather(
                _write(), *[_read() for _ in range(K_READS)], return_exceptions=True,
            )

            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"concurrent claim-race raised: {errors[:3]}"

            write_result, read_results = results[0], results[1:]
            assert write_result, "supersede_claim itself failed (returned falsy)"

            for r in read_results:
                # A torn/inconsistent read would show up as applicable=True
                # together with a non-empty failed_constraints (or the
                # reverse) -- assert internal consistency on every single
                # read, whichever side of the race it landed on.
                if r.applicable:
                    assert not r.failed_constraints, (
                        f"torn read: applicable=True but failed_constraints={r.failed_constraints!r}"
                    )
                else:
                    assert r.failed_constraints, (
                        "torn read: applicable=False but no failed_constraints recorded"
                    )

            # Final state, read after everything settles, must reflect the
            # supersession completed -- the precondition's specific claim_id
            # is now superseded (truth_state OUT), so the procedure must no
            # longer be applicable via this claim.
            final = await check_hard_constraints(pool, dict(procedure))
            assert final.applicable is False, (
                "after supersession settled, the procedure is still applicable via the "
                "now-superseded claim -- the write did not take effect"
            )
        finally:
            await _cleanup(pool)
            await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", f"{PREFIX}-claim-race-skill")
            await pool.close()

    _run(_body())


# --- 4. fault injection ---

def test_embedding_provider_timeout_during_claim_capture_leaves_no_orphaned_row():
    """Fault injection: the embedding provider times out mid-capture_
    claim(). capture_claim() calls embedder.embed_one() BEFORE opening
    its DB transaction (see claims.py) -- so the correct, provably safe
    behavior is: the exception propagates (the failure is VISIBLE, not
    silently swallowed into a false success), and NO knowledge_nodes/
    edges row is ever written, because the transaction never began.
    This test proves that positively against the real function, not by
    reading the source and assuming it."""

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                f"{PREFIX}-fault-task", f"{PREFIX}-fault-skill",
            )
            subject = f"{PREFIX}:fault-subject"

            raised = False
            try:
                await capture_claim(
                    pool, statement=f"{PREFIX} {subject} should never be written",
                    task_ids=[f"{PREFIX}-fault-skill"],
                    subject=subject, predicate="status", object="unreachable",
                    embedder=RaisingEmbedder(),
                )
            except asyncio.TimeoutError:
                raised = True

            assert raised, (
                "capture_claim() swallowed the embedder failure instead of raising it -- "
                "a background failure must be visible, never a silent false success"
            )

            orphaned = await pool.fetchval(
                "SELECT count(*) FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%fault-subject%",
            )
            assert orphaned == 0, (
                f"found {orphaned} orphaned row(s) after a failed capture_claim() -- "
                "a partial write survived the embedder failure"
            )
        finally:
            await _cleanup(pool)
            await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", f"{PREFIX}-fault-skill")
            await pool.close()

    _run(_body())
