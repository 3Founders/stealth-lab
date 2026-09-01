"""
Proving test for the "Ideal V1" directive: a freshly captured LOCAL
candidate procedure must be able to accumulate real evidence toward
verified status without deadlocking on a verified-only retrieval default.

This is an END-TO-END composition test over REAL code, no faking of the
store or the ranking policy itself:
  - app.local_agent.local_store.LocalProcedureStore (real sqlite3 file via
    tmp_path -- NOT ":memory:", per the store's own per-call `_connect()`
    behavior: an in-memory db does not persist across separate method
    calls on the same store instance, since each call opens a fresh
    connection to a fresh, empty in-memory database).
  - app.local_agent.unified_retrieval.rank_unified_candidates (the real,
    pure ranking policy that decides what "surfaces" during retrieval).
  - app.local_agent.local_applicability.check_local_hard_constraints (the
    real DB-free hard-gate cascade rank_unified_candidates calls).
  - app.services.procedures.MIN_SUCCESSES_FOR_VERIFIED /
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED (the real, single-sourced
    promotion thresholds -- not a second copy).

FINDING: no bug. Every step of the directive's four-part question (a)-(d)
already holds against the real code as it stands today:
  (a) a fresh candidate IS retrievable via unified retrieval whenever the
      caller does not force require_verified=True (search_local_procedures
      never filters by verification_state; check_local_hard_constraints
      only excludes a candidate when require_verified=True).
  (b) it accumulates real record_local_execution_outcome calls, one row
      per call, real sqlite persistence across separate connections.
  (c) it actually transitions candidate -> verified once real recorded
      successes cross MIN_SUCCESSES_FOR_VERIFIED with zero failures across
      >= MIN_DISTINCT_CONTEXTS_FOR_VERIFIED distinct contexts (the exact
      arithmetic record_local_execution_outcome implements).
  (d) once verified, it is RANKED (not merely permitted) ahead of a
      lower-capability verified alternative from the other source, via
      rank_unified_candidates' real (verification_rank, capability_bucket,
      ...) sort key -- and it is also newly INCLUDED under
      require_verified=True, which excluded it before maturation.

No fix was made to local_store.py, applicability.py, or
unified_retrieval.py for this task -- this file only adds the proving
test the directive asked for.
"""
from __future__ import annotations

from app.local_agent.local_applicability import check_local_hard_constraints
from app.local_agent.local_store import LocalProcedureStore
from app.local_agent.unified_retrieval import rank_unified_candidates
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
)


def _store(tmp_path) -> LocalProcedureStore:
    # Real file path, not ":memory:" -- LocalProcedureStore._connect()
    # opens a fresh sqlite3.Connection per method call; an in-memory db
    # would not persist state across the separate capture/record/get
    # calls this test makes on the same store instance.
    return LocalProcedureStore(db_path=str(tmp_path / "local_procedures.db"))


def _global_proc(**overrides) -> dict:
    base = {
        "id": "global-weak", "procedure_id": "global-weak",
        "name": "weaker global alternative", "goal": "do the thing, less reliably",
        "verification_state": "verified", "staleness": "fresh",
        "availability": "active", "scope_type": "global",
        "verification_stats": {"attempts": 20, "successes": 8},  # p = 0.40
    }
    base.update(overrides)
    return base


def test_candidate_procedure_matures_end_to_end_without_deadlocking_on_verified_only_default(tmp_path):
    store = _store(tmp_path)

    # -----------------------------------------------------------------
    # Step 0: capture -- a brand-new local procedure is born
    # candidate/fresh/active, same "nothing is born verified/trusted"
    # posture the global writer uses.
    # -----------------------------------------------------------------
    captured = store.capture_local_procedure(
        name="repo-specific-fix", goal="fix the repo-specific thing",
        provenance="system_pending_review",
        scope_type="repository", scope_entity_id="repo-1",
    )
    row_id = captured["id"]

    fresh_row = store.get_local_procedure(row_id)
    assert fresh_row["verification_state"] == "candidate"
    assert fresh_row["staleness"] == "fresh"
    assert fresh_row["availability"] == "active"
    assert fresh_row["verification_stats"]["attempts"] == 0

    # -----------------------------------------------------------------
    # (a) Retrievable at all via unified retrieval when require_verified
    # is not forced -- search_local_procedures never filters by
    # verification_state, and the hard-gate cascade only excludes a
    # candidate when require_verified=True.
    # -----------------------------------------------------------------
    lexical_hits = store.search_local_procedures("repo-specific fix")
    assert len(lexical_hits) == 1
    assert lexical_hits[0]["id"] == row_id

    explicit_result = check_local_hard_constraints(fresh_row, require_verified=False)
    assert explicit_result.applicable is True

    ranked_explicit = rank_unified_candidates(
        [fresh_row], [_global_proc()], require_verified=False,
    )
    surfaced_sources = {r.source for r in ranked_explicit}
    assert "local" in surfaced_sources, (
        "a fresh candidate must surface under an explicit-invocation / "
        "require_verified=False caller -- this is the 'candidate remains "
        "explicitly invocable' half of ticket 13's rule"
    )

    # And, by the SAME rule in the other direction: a caller doing
    # automatic selection (require_verified=True, the retrieval default)
    # must NOT see this still-unverified candidate yet -- proving the
    # gate is real, not a no-op, before we test that it lifts later.
    ranked_auto_before = rank_unified_candidates(
        [fresh_row], [_global_proc()], require_verified=True,
    )
    assert [r.source for r in ranked_auto_before] == ["global"], (
        "an unverified candidate must not surface under the automatic, "
        "verified-only retrieval default"
    )

    # -----------------------------------------------------------------
    # (b) Accumulates real record_local_execution_outcome calls, across
    # separate connections (this is the sqlite persistence wrinkle the
    # directive calls out -- tmp_path, not :memory:, makes this real).
    # -----------------------------------------------------------------
    # One success short of the threshold, only 2 distinct contexts: must
    # NOT verify yet -- proves the transition is threshold-gated, not
    # "any success flips it".
    partial = None
    for i in range(MIN_SUCCESSES_FOR_VERIFIED - 1):
        partial = store.record_local_execution_outcome(
            row_id=row_id, success=True, context_key=f"ctx-{i % 2}", steps_used=4,
        )
    assert partial["verification_state"] == "candidate"
    assert partial["verification_stats"]["successes"] == MIN_SUCCESSES_FOR_VERIFIED - 1
    assert partial["verification_stats"]["distinct_contexts"] == 2

    # Still excluded from the automatic default while still a candidate.
    ranked_auto_partial = rank_unified_candidates(
        [partial], [_global_proc()], require_verified=True,
    )
    assert [r.source for r in ranked_auto_partial] == ["global"]

    # -----------------------------------------------------------------
    # (c) Crosses the real threshold -- >= MIN_SUCCESSES_FOR_VERIFIED
    # successes, zero failures, >= MIN_DISTINCT_CONTEXTS_FOR_VERIFIED
    # distinct contexts -- and actually transitions to "verified".
    # -----------------------------------------------------------------
    matured = store.record_local_execution_outcome(
        row_id=row_id, success=True, context_key="ctx-2", steps_used=4,
    )
    assert matured["verification_state"] == "verified"
    assert matured["verification_stats"]["successes"] == MIN_SUCCESSES_FOR_VERIFIED
    assert matured["verification_stats"]["distinct_contexts"] == MIN_DISTINCT_CONTEXTS_FOR_VERIFIED
    assert matured["verification_stats"]["attempts"] - matured["verification_stats"]["successes"] == 0

    # Persisted for real -- re-fetching via a brand-new connection (a
    # fresh _connect() call) sees the same matured state, not an
    # in-process-only mutation.
    refetched = store.get_local_procedure(row_id)
    assert refetched["verification_state"] == "verified"

    # -----------------------------------------------------------------
    # (d) Now verified, it is both newly INCLUDED under the automatic
    # require_verified=True default, and RANKED ahead of a lower-quality
    # verified alternative from the other source (p=1.0 over 10 attempts
    # vs. the global alternative's p=0.40 over 20).
    # -----------------------------------------------------------------
    weak_global = _global_proc()
    ranked_after = rank_unified_candidates(
        [refetched], [weak_global], require_verified=True,
    )
    assert len(ranked_after) == 2
    assert ranked_after[0].source == "local", (
        "a matured, higher-capability local candidate must outrank a "
        "lower-capability verified alternative once it is itself verified"
    )
    assert ranked_after[0].procedure["id"] == row_id


def test_multiple_local_candidates_mature_independently_by_real_context_diversity(tmp_path):
    """A second, narrower proof that the maturation arithmetic is driven
    by real distinct context_keys recorded through the real store, not by
    raw call count alone: a candidate hammered with the SAME context_key
    over and over never crosses MIN_DISTINCT_CONTEXTS_FOR_VERIFIED and so
    never verifies, even with far more than MIN_SUCCESSES_FOR_VERIFIED
    successes -- while a sibling candidate recording the same number of
    successes across enough distinct contexts does."""
    store = _store(tmp_path)

    narrow = store.capture_local_procedure(
        name="narrow-context-only", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
    )
    diverse = store.capture_local_procedure(
        name="diverse-context", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
    )

    narrow_result = None
    for _ in range(MIN_SUCCESSES_FOR_VERIFIED + 5):
        narrow_result = store.record_local_execution_outcome(
            row_id=narrow["id"], success=True, context_key="only-ever-this-one",
        )
    assert narrow_result["verification_state"] == "candidate", (
        "success count alone must not verify a procedure -- distinct "
        "context diversity is a real, separately-enforced requirement"
    )
    assert narrow_result["verification_stats"]["distinct_contexts"] == 1

    diverse_result = None
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        diverse_result = store.record_local_execution_outcome(
            row_id=diverse["id"], success=True,
            context_key=f"ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}",
        )
    assert diverse_result["verification_state"] == "verified"
