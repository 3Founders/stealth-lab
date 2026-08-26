"""
DB-free coverage for applicability.py's non-compensatory disqualification
cascade itself (check_hard_constraints' individual gates, the pure
_scope_matches/_excluded helpers, and find_applicable_procedures' two
early-return paths). Closes a real gap: every one of these branches was
previously exercised ONLY by test_applicability_e2e.py against a real
Postgres instance -- none of them actually touch the database before the
point they disqualify (pool is only ever reached inside the precondition
loop's project_state() call), so a FakePool proves the cascade's own
decision logic even when the shared instance is unreachable or drifted,
which this repo's board has logged as a recurring, non-hypothetical gap.

Same FakePool shape as test_applicability_cascade_offline.py (kept
separate: that file owns the 1.8c memoization/cold-start-gate story, this
one owns the cascade's own disqualification decisions).
"""
import asyncio

from app.services.access import AccessScope
from app.services.applicability import (
    _excluded,
    _scope_matches,
    check_hard_constraints,
    find_applicable_procedures,
)

SUBJECT = "project:p"


def _procedure(proc_id="00000000-0000-4000-8000-000000000001", **overrides):
    row = {
        "id": proc_id,
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "approval_status": "approved",
        "scope": {},
        "exclusions": [],
        "preconditions": [],
        "invariants": [],
    }
    row.update(overrides)
    return row


class FakePool:
    """Records every call; only ever reached by the precondition loop's
    project_state() fetch (`fetch`) -- never by the checks these tests
    target, which is exactly the property under test for several cases
    below (assert zero fetches)."""

    def __init__(self, *, claims=(), procedures=(), ranked=(), verified_count=0):
        self._claims = list(claims)
        self._procedures = list(procedures)
        self._ranked = list(ranked)
        self._verified_count = verified_count
        self.fetch_calls = []
        self.fetchval_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        if "embedding <=>" in sql and "1 -" in sql:
            return self._ranked
        if " FROM procedures" in sql:
            return self._procedures
        return self._claims

    async def fetchval(self, sql, *params):
        self.fetchval_calls.append((" ".join(sql.split()), params))
        return self._verified_count


def _run(coro):
    return asyncio.run(coro)


# --- check_hard_constraints: each disqualifying gate, none reach the DB ---

def test_expired_procedure_is_disqualified_on_temporal_validity_alone():
    from datetime import datetime, timedelta, timezone
    procedure = _procedure(t_invalid=datetime.now(timezone.utc) - timedelta(days=1))
    result = _run(check_hard_constraints(FakePool(), procedure))
    assert not result.applicable
    assert result.failed_constraints == ["temporal_validity"]


def test_stale_procedure_is_disqualified_even_though_everything_else_passes():
    pool = FakePool()
    procedure = _procedure(staleness="stale")
    result = _run(check_hard_constraints(pool, procedure))
    assert not result.applicable
    assert result.failed_constraints == ["staleness"]
    assert pool.fetch_calls == []  # disqualified before any DB reach


def test_non_active_availability_is_disqualified():
    procedure = _procedure(availability="quarantined")
    result = _run(check_hard_constraints(FakePool(), procedure))
    assert result.failed_constraints == ["availability"]


def test_unverified_procedure_disqualified_when_verification_is_required():
    procedure = _procedure(verification_state="proposed")
    result = _run(check_hard_constraints(FakePool(), procedure, require_verified=True))
    assert result.failed_constraints == ["verification_state"]


def test_unapproved_procedure_disqualified_when_verification_is_required():
    procedure = _procedure(approval_status="proposed")
    result = _run(check_hard_constraints(FakePool(), procedure, require_verified=True))
    assert result.failed_constraints == ["approval_status"]


def test_explicit_invocation_bypasses_both_verification_and_approval_gates():
    """Ticket 13's own rule: require_verified=False (explicit invocation
    by id) must not be blocked by either gate -- only automatic selection
    requires both real evidence and a human sign-off."""
    procedure = _procedure(verification_state="proposed", approval_status="proposed")
    result = _run(check_hard_constraints(FakePool(), procedure, require_verified=False))
    assert result.applicable


def test_scope_mismatch_disqualifies_via_the_cascade():
    procedure = _procedure(scope={"repo": ["backend"]})
    result = _run(check_hard_constraints(FakePool(), procedure, current_scope={"repo": ["frontend"]}))
    assert result.failed_constraints == ["scope"]


def test_matching_scope_passes_the_cascade():
    procedure = _procedure(scope={"repo": ["backend"]})
    result = _run(check_hard_constraints(FakePool(), procedure, current_scope={"repo": ["backend"]}))
    assert result.applicable


def test_exclusion_match_disqualifies_even_with_matching_scope():
    procedure = _procedure(exclusions=[{"key": "file_pattern", "values": ["*.generated.py"]}])
    result = _run(check_hard_constraints(
        FakePool(), procedure, current_scope={"file_pattern": ["*.generated.py"]},
    ))
    assert result.failed_constraints == ["exclusions"]


def test_malformed_precondition_missing_subject_is_skipped_not_disqualifying():
    """Not this function's job to validate authoring -- a precondition
    entry with no subject is silently skipped, and critically never
    reaches the DB (pool.fetch stays empty), unlike every well-formed
    precondition which would trigger a project_state() fetch."""
    pool = FakePool()
    procedure = _procedure(preconditions=[{"predicate": "has_test_runner", "object": "pytest"}])
    result = _run(check_hard_constraints(pool, procedure))
    assert result.applicable
    assert pool.fetch_calls == []


def test_invariant_violation_disqualifies_with_no_preconditions_involved():
    procedure = _procedure(invariants=[{"kind": "numeric", "expr": "amount <= balance"}])
    result = _run(check_hard_constraints(
        FakePool(), procedure, invariant_bindings={"amount": 200, "balance": 100},
    ))
    assert not result.applicable
    assert result.failed_constraints == ["invariant:amount <= balance"]


def test_unbound_invariant_does_not_disqualify():
    """The retrieval-time normal case (ticket 12's asymmetry, mirrored
    here at the cascade's own call site): nobody has bound a quantity
    yet, so the procedure stays applicable."""
    procedure = _procedure(invariants=[{"kind": "numeric", "expr": "amount <= balance"}])
    result = _run(check_hard_constraints(FakePool(), procedure))
    assert result.applicable


# --- pure helpers: _scope_matches / _excluded, no pool involved at all ---

def test_scope_matches_true_when_procedure_scope_is_empty():
    assert _scope_matches({}, {"repo": ["frontend"]})


def test_scope_matches_true_on_list_overlap():
    assert _scope_matches({"repo": ["backend", "shared"]}, {"repo": ["frontend", "shared"]})


def test_scope_matches_false_when_caller_omits_a_required_key():
    assert not _scope_matches({"repo": ["backend"]}, {})


def test_scope_matches_false_on_no_overlap():
    assert not _scope_matches({"repo": ["backend"]}, {"repo": ["frontend"]})


def test_scope_matches_handles_bare_string_values_on_both_sides():
    assert _scope_matches({"repo": "backend"}, {"repo": "backend"})
    assert not _scope_matches({"repo": "backend"}, {"repo": "frontend"})


def test_scope_matches_a_key_the_procedure_does_not_mention_is_unconstrained():
    assert _scope_matches({"repo": ["backend"]}, {"repo": ["backend"], "env": ["prod"]})


def test_excluded_false_when_no_exclusions_configured():
    assert not _excluded([], {"file_pattern": ["*.generated.py"]})


def test_excluded_false_when_current_scope_lacks_the_excluded_key():
    """The 'continue' branch: an exclusion naming a key the caller never
    supplied imposes nothing -- distinct from a present-but-non-
    overlapping key, which also does not exclude."""
    assert not _excluded([{"key": "file_pattern", "values": ["*.generated.py"]}], {})


def test_excluded_true_on_overlap_incl_bare_string_values():
    assert _excluded([{"key": "file_pattern", "values": "*.generated.py"}],
                      {"file_pattern": "*.generated.py"})


# --- find_applicable_procedures: both early-return paths, no candidates
# fetched or ranked once the cascade or the cold-start gate closes it ---

def test_cold_start_gate_returns_empty_before_ever_fetching_candidates():
    pool = FakePool(verified_count=0)
    results = _run(find_applicable_procedures(pool, current_scope={}))
    assert results == []
    assert all(" FROM procedures" not in c[0] for c in pool.fetch_calls), (
        "gate must close before the candidate fetch ever runs"
    )


def test_no_survivors_after_the_cascade_returns_empty_list_not_none():
    pool = FakePool(
        verified_count=5,
        procedures=[_procedure(staleness="stale")],
    )
    results = _run(find_applicable_procedures(pool, current_scope={}))
    assert results == []


def test_goal_embedding_ranks_survivors_and_appends_unranked_ones_last():
    """The merge in find_applicable_procedures' embedding branch: ranked
    survivors come back in similarity order, and a survivor the ranking
    query didn't return (no embedding stored) is appended afterward
    rather than silently dropped."""
    ranked_proc = _procedure("00000000-0000-4000-8000-000000000001")
    unranked_proc = _procedure("00000000-0000-4000-8000-000000000002")
    pool = FakePool(
        verified_count=5,
        procedures=[ranked_proc, unranked_proc],
        ranked=[{"id": "00000000-0000-4000-8000-000000000001", "similarity": 0.9}],
    )

    results = _run(find_applicable_procedures(
        pool, current_scope={}, goal_embedding=[0.1] * 8, limit=10,
    ))

    assert [r["id"] for r in results] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]
    assert results[0]["_similarity_score"] == 0.9
    assert "_similarity_score" not in results[1]
