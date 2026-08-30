"""The cold-start gate must yield to an explicit unverified opt-in.

CONTEXT -- the chicken-and-egg this fixes, measured with a real model
(gemma-4-31B-it) against a real repo on 2026-08-28:

    retrieval is disabled until >=1 VERIFIED procedure exists
 -> a procedure is verified only by accruing execution evidence
 -> evidence accrues only when a matched procedure is actually USED
 -> nothing can ever match, so nothing can ever become verified.

find_applicable_procedures ran should_disable_procedure_retrieval() BEFORE
consulting require_verified, so find_best_way's `allow_unverified_procedures`
opt-in (ticket 13's named path) was unreachable: a freshly-extracted
procedure stayed invisible even with the flag set, and matched immediately
once only that gate was bypassed. One blocker, isolated -- the NULL
embedding on the same row was NOT what stopped it.

The DEFAULT is deliberately unchanged: every require_verified=True caller
still hits the gate, including retrieve_precedent's own path.

Fully offline -- a FakePool records which queries were attempted.
"""
from __future__ import annotations

import pytest

import app.services.applicability as ap


class FakePool:
    """Records fetches. Returning [] for the candidate SELECT is fine --
    these tests assert on WHETHER the query was reached, not its rows."""

    def __init__(self):
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return []

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        return 0

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        return None


def _reached_candidate_query(pool: FakePool) -> bool:
    return any("FROM procedures" in q for q in pool.queries)


@pytest.fixture
def gate_says_disable(monkeypatch):
    """Cold start: zero verified procedures, so the gate wants retrieval off."""
    async def _disable(*a, **k):
        return True
    monkeypatch.setattr(ap, "should_disable_procedure_retrieval", _disable)


@pytest.mark.asyncio
async def test_default_caller_is_still_gated(gate_says_disable):
    """THE default must not change. require_verified=True -> short-circuit."""
    pool = FakePool()
    out = await ap.find_applicable_procedures(
        pool, goal_embedding=None, current_scope={}, require_verified=True,
    )
    assert out == []
    assert not _reached_candidate_query(pool), (
        "a default caller must short-circuit before querying procedures"
    )


@pytest.mark.asyncio
async def test_explicit_optin_bypasses_the_gate(gate_says_disable):
    """The fix: an explicit opt-in reaches the candidate query even in cold
    start. Opting in IS the cold-start case."""
    pool = FakePool()
    await ap.find_applicable_procedures(
        pool, goal_embedding=None, current_scope={}, require_verified=False,
    )
    assert _reached_candidate_query(pool), (
        "require_verified=False must proceed past the cold-start gate -- "
        "otherwise allow_unverified_procedures is an unreachable flag"
    )


@pytest.mark.asyncio
async def test_gate_still_consulted_when_it_permits(monkeypatch):
    """When the gate permits, both modes proceed -- the fix must not make
    the gate dead code for the default path."""
    calls = []

    async def _permit(*a, **k):
        calls.append(True)
        return False

    monkeypatch.setattr(ap, "should_disable_procedure_retrieval", _permit)
    pool = FakePool()
    await ap.find_applicable_procedures(
        pool, goal_embedding=None, current_scope={}, require_verified=True,
    )
    assert calls, "the gate must still be evaluated for default callers"
    assert _reached_candidate_query(pool)


@pytest.mark.asyncio
async def test_optin_does_not_even_evaluate_the_gate(gate_says_disable, monkeypatch):
    """Short-circuit ordering: `require_verified and await ...` means the
    opt-in path skips the gate's own query entirely, not just its result.
    Cheap, and it keeps the opt-in independent of gate cost."""
    evaluated = []

    async def _tracked(*a, **k):
        evaluated.append(True)
        return True

    monkeypatch.setattr(ap, "should_disable_procedure_retrieval", _tracked)
    pool = FakePool()
    await ap.find_applicable_procedures(
        pool, goal_embedding=None, current_scope={}, require_verified=False,
    )
    assert evaluated == [], "opt-in must not pay for the gate query at all"


def test_retrieve_precedent_path_keeps_its_own_gate():
    """verified_procedure_candidates() -- retrieve_precedent's path -- has a
    SEPARATE, deliberately untouched call to the same gate. Surfacing an
    unverified procedure there would let a caller read 'precedent found' as
    an implicit reuse signal without going through check_procedure's
    cascade, which is the founder ruling that path documents."""
    import inspect
    src = inspect.getsource(ap.verified_procedure_candidates)
    assert "should_disable_procedure_retrieval" in src
    assert "require_verified and await" not in src, (
        "retrieve_precedent's gate must stay unconditional"
    )
