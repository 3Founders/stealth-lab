"""
DB-free coverage for state.py's two exported entry points. project_state()
already gets exercised indirectly by applicability's offline cascade
tests and by the WAVE-3 tenancy sweep tests, but two of its own edge
branches (empty subjects, default as_of) were never hit by either, and
state_delta() -- computed on demand from two project_state() calls, per
its own docstring -- had zero offline coverage at all before this file:
every existing caller of it lives in test_state_e2e.py, which needs a
real Postgres instance.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app.services.state import project_state, state_delta

SUBJECT = "project:p"


def _claim(claim_id, predicate="has_test_runner", obj="pytest"):
    return {
        "id": claim_id,
        "properties": {"subject": SUBJECT, "predicate": predicate, "object": obj},
        "t_valid": None,
        "t_invalid": None,
    }


class FakePool:
    """Keys claim rows by the as_of timestamp passed in, so a test can
    give project_state() a different answer at two different instants --
    exactly what state_delta's two internal calls need to be exercised
    meaningfully rather than trivially (same snapshot both times)."""

    def __init__(self, claims_by_as_of: dict):
        self._claims_by_as_of = claims_by_as_of
        self.fetch_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        as_of = params[1]
        return self._claims_by_as_of.get(as_of, [])


def _run(coro):
    return asyncio.run(coro)


def test_empty_subjects_returns_empty_without_touching_the_pool():
    pool = FakePool({})
    result = _run(project_state(pool, subjects=[]))
    assert result == []
    assert pool.fetch_calls == []


def test_as_of_defaults_to_now_when_omitted():
    """No explicit as_of supplied -- the function must still produce a
    real, usable timestamp (not None) in the query params."""
    now_before = datetime.now(timezone.utc)
    pool = FakePool({})
    _run(project_state(pool, subjects=[SUBJECT]))
    now_after = datetime.now(timezone.utc)

    assert len(pool.fetch_calls) == 1
    used_as_of = pool.fetch_calls[0][1][1]
    assert now_before <= used_as_of <= now_after


def test_state_delta_reports_added_and_removed_and_leaves_unchanged_out():
    before = datetime.now(timezone.utc) - timedelta(days=1)
    after = datetime.now(timezone.utc)
    pool = FakePool({
        before: [_claim("stays"), _claim("removed", predicate="old_fact")],
        after: [_claim("stays"), _claim("added", predicate="new_fact")],
    })

    delta = _run(state_delta(pool, subjects=[SUBJECT], before=before, after=after))

    assert [c["id"] for c in delta["added"]] == ["added"]
    assert [c["id"] for c in delta["removed"]] == ["removed"]


def test_state_delta_with_no_change_reports_both_lists_empty():
    before = datetime.now(timezone.utc) - timedelta(days=1)
    after = datetime.now(timezone.utc)
    same_claims = [_claim("unchanged")]
    pool = FakePool({before: same_claims, after: same_claims})

    delta = _run(state_delta(pool, subjects=[SUBJECT], before=before, after=after))

    assert delta == {"added": [], "removed": []}


def test_state_delta_threads_scope_to_both_calls_identically():
    """A delta computed from two differently-scoped snapshots would leak
    existence information -- pin that both project_state() calls receive
    the exact same scope/tenant_scope the caller passed."""
    from app.services.access import AccessScope, TenantScope

    before = datetime.now(timezone.utc) - timedelta(days=1)
    after = datetime.now(timezone.utc)
    pool = FakePool({before: [], after: []})
    scope = AccessScope.for_user("viewer-1")
    tenant = TenantScope.for_tenant("org-1")

    _run(state_delta(
        pool, subjects=[SUBJECT], before=before, after=after,
        scope=scope, tenant_scope=tenant,
    ))

    assert len(pool.fetch_calls) == 2
    first_params, second_params = pool.fetch_calls[0][1], pool.fetch_calls[1][1]
    assert first_params[2:] == second_params[2:]  # identical scope bindings both calls
