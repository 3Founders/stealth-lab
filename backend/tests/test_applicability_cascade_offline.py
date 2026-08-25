"""
DB-free unit tests for applicability.py's ticket-1.8c work: the
per-cascade project_state() memoization and the tenant-scoped
cold-start gate. A fake pool records every query so the tests prove
CALL COUNTS and QUERY CONTENT -- things the live e2e suite
(test_applicability_e2e.py) cannot isolate against real Postgres.
"""
import asyncio

from datetime import datetime, timezone

from app.services.access import AccessScope
from app.services.applicability import (
    check_hard_constraints,
    find_applicable_procedures,
    should_disable_procedure_retrieval,
)

SUBJECT = "project:p"


def _claim(predicate, obj):
    """Row shape project_state() maps over -- dict is duck-compatible
    with asyncpg.Record for the [key] access it does."""
    return {
        "id": f"claim-{predicate}",
        "properties": {"subject": SUBJECT, "predicate": predicate, "object": obj},
        "t_valid": None,
        "t_invalid": None,
    }


def _procedure(proc_id, preconditions, **overrides):
    row = {
        "id": proc_id,
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "approval_status": "approved",
        "scope": {},
        "exclusions": [],
        "preconditions": preconditions,
        "invariants": [],
    }
    row.update(overrides)
    return row


class FakePool:
    """
    Answers the two query shapes the cascade issues:
      - `FROM procedures` (candidate fetch / cold-start count)
      - knowledge_nodes claims fetch (project_state)
    and RECORDS each call. The candidate fetch returns whatever
    procedure rows were configured; claim fetches return configured
    claims -- so a test can count exactly how many times the projection
    was (re-)fetched.
    """

    def __init__(self, *, claims=(), procedures=(), verified_count=0):
        self._claims = list(claims)
        self._procedures = list(procedures)
        self._verified_count = verified_count
        self.fetch_calls = []
        self.fetchval_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        if " FROM procedures" in sql:
            return self._procedures
        return self._claims

    async def fetchval(self, sql, *params):
        self.fetchval_calls.append((" ".join(sql.split()), params))
        return self._verified_count


def _pre(subject=SUBJECT, predicate="has_test_runner", obj="pytest"):
    return {"subject": subject, "predicate": predicate, "object": obj}


def test_shared_subject_across_candidates_is_fetched_exactly_once():
    """The 1.8c payoff: N candidates preconditioned on the same subject
    cost ONE projection fetch per cascade, not N. (The caller honors the
    documented contract by pinning one as_of across the cascade -- a
    fresh now() per call would put different timestamps in the cache
    keys, which is exactly what the contract says not to do.)"""
    pool = FakePool(claims=[_claim("has_test_runner", "pytest")])
    candidates = [
        _procedure(f"00000000-0000-4000-8000-00000000000{i}", [_pre()]) for i in (1, 2, 3)
    ]
    as_of = datetime.now(timezone.utc)

    async def _run():
        cache = {}
        results = []
        for c in candidates:
            results.append(await check_hard_constraints(pool, c, as_of=as_of, state_cache=cache))
        return results

    results = asyncio.run(_run())
    assert all(r.applicable for r in results)
    state_fetches = [c for c in pool.fetch_calls if " FROM procedures" not in c[0]]
    assert len(state_fetches) == 1


def test_without_a_shared_cache_each_candidate_pays_for_itself():
    """Pins that the dedupe comes from the SHARED cache, not from some
    accidental global: separate cascades (the documented validity scope)
    each fetch their own projection."""
    pool = FakePool(claims=[_claim("has_test_runner", "pytest")])
    candidates = [
        _procedure(f"00000000-0000-4000-8000-00000000000{i}", [_pre()]) for i in (1, 2)
    ]

    async def _run():
        return [
            await check_hard_constraints(pool, c) for c in candidates  # no cache passed
        ]

    assert all(r.applicable for r in asyncio.run(_run()))
    state_fetches = [c for c in pool.fetch_calls if " FROM procedures" not in c[0]]
    assert len(state_fetches) == 2


def test_repeat_subjects_inside_one_procedure_are_deduped_even_without_shared_cache():
    pool = FakePool(claims=[
        _claim("has_test_runner", "pytest"),
        _claim("language", "python"),
    ])
    procedure = _procedure(
        "00000000-0000-4000-8000-000000000001",
        [_pre(predicate="has_test_runner", obj="pytest"),
         _pre(predicate="language", obj="python")],
    )

    result = asyncio.run(check_hard_constraints(pool, procedure))

    assert result.applicable
    state_fetches = [c for c in pool.fetch_calls if " FROM procedures" not in c[0]]
    assert len(state_fetches) == 1


def test_caching_never_changes_the_decision_itself():
    """A memo must be transparent: with NO matching claims, cached-empty
    behaves exactly like uncached-empty -- both candidates fail closed
    under CWA on one shared fetch."""
    pool = FakePool(claims=[])
    candidates = [
        _procedure(f"00000000-0000-4000-8000-00000000000{i}", [_pre()]) for i in (1, 2)
    ]
    as_of = datetime.now(timezone.utc)

    async def _run():
        cache = {}
        return [
            await check_hard_constraints(pool, c, as_of=as_of, state_cache=cache)
            for c in candidates
        ]

    results = asyncio.run(_run())
    assert all(not r.applicable for r in results)
    assert all(r.failed_constraints[0].startswith("precondition:") for r in results)
    state_fetches = [c for c in pool.fetch_calls if " FROM procedures" not in c[0]]
    assert len(state_fetches) == 1


def test_distinct_subjects_are_distinct_cache_entries_not_conflated():
    other = "project:other"
    pool = FakePool(claims=[_claim("has_test_runner", "pytest"), _claim("language", "python")])
    procedure = _procedure(
        "00000000-0000-4000-8000-000000000001",
        [_pre(), {"subject": other, "predicate": "language", "object": "python"}],
    )

    result = asyncio.run(check_hard_constraints(pool, procedure))

    assert result.applicable
    state_fetches = [c for c in pool.fetch_calls if " FROM procedures" not in c[0]]
    assert len(state_fetches) == 2
    fetched_subjects = {c[1][0][0] for c in state_fetches}
    assert fetched_subjects == {SUBJECT, other}


# --- tenant-scoped cold-start gate ---

def test_cold_start_gate_filters_the_count_by_viewer_visibility():
    """Ticket 09's rule made real here too: the count query carries
    access.py's visibility predicate, parameterized by THIS viewer --
    another tenant's verified procedures must not end this viewer's
    cold start."""
    pool = FakePool(verified_count=0)

    disabled = asyncio.run(
        should_disable_procedure_retrieval(pool, AccessScope.for_user("tenant-7"))
    )

    assert disabled is True  # zero visible verified < threshold
    sql, params = pool.fetchval_calls[0]
    assert "visibility" in sql
    assert "owner_id" in sql
    assert list(params) == ["tenant-7"]


def test_default_gate_keeps_the_unrestricted_global_count():
    """Back-compat contract: internal callers passing no scope get the
    literal TRUE fragment -- visibly permissive, zero parameters --
    exactly as before 1.8c."""
    pool = FakePool(verified_count=5)

    disabled = asyncio.run(should_disable_procedure_retrieval(pool))

    assert disabled is False
    sql, params = pool.fetchval_calls[0]
    assert " AND TRUE" in sql
    assert list(params) == []


# --- find_applicable_procedures end-to-end wiring (offline) ---

def test_full_cascade_shares_one_cache_and_scopes_its_gate():
    """The pipeline wiring itself: the cold-start gate receives the
    caller's access scope, and ONE memo table serves the whole
    candidate loop -- three same-subject candidates produce one claim
    fetch after the candidate fetch."""
    procedures = [
        _procedure(f"00000000-0000-4000-8000-00000000000{i}", [_pre()]) for i in (1, 2, 3)
    ]
    pool = FakePool(
        claims=[_claim("has_test_runner", "pytest")],
        procedures=procedures,
        verified_count=99,  # gate open for this viewer
    )

    survivors = asyncio.run(
        find_applicable_procedures(
            pool, current_scope={}, access_scope=AccessScope.for_user("tenant-7"),
        )
    )

    assert len(survivors) == 3
    gate_sql, gate_params = pool.fetchval_calls[0]
    assert "owner_id" in gate_sql and "visibility" in gate_sql, (
        "gate must carry the visibility filter for this viewer"
    )
    assert list(gate_params) == ["tenant-7"]
    assert len(pool.fetch_calls) == 2, (
        "one candidate fetch + ONE shared claim fetch -- not one claim fetch per candidate"
    )