"""
DB-free coverage for registry.py's own Python-side decision logic:
create_extractor_version's kind validation (raises before ever touching
the pool), select_extractor's scope filter + version/kind tiebreak sort,
and extractor_stats' division-by-zero guards. All of this previously had
zero offline coverage -- test_procedure_extraction_registry_e2e.py proves
the same behaviors, correctly, but only against a real Postgres instance
via actual INSERTs; a FakePool proves the selection/sort/arithmetic is
right independent of whether the shared DB instance is reachable.
"""
import asyncio

import pytest

from app.services.procedure_extraction.registry import (
    create_extractor_version,
    extractor_stats,
    select_extractor,
)


def _run(coro):
    return asyncio.run(coro)


def _row(name="ext", version="1", kind="deterministic", scope=None):
    return {
        "id": f"id-{name}-{version}", "name": name, "description": "",
        "kind": kind, "version": version, "config": {}, "scope": scope or {},
    }


class FakePool:
    def __init__(self, *, rows=(), stats_rows=()):
        self._rows = list(rows)
        self._stats_rows = list(stats_rows)  # consumed in order: [counts, outcomes]
        self.fetch_calls = 0
        self.fetchrow_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls += 1
        return self._rows

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((" ".join(sql.split()), params))
        return self._stats_rows[len(self.fetchrow_calls) - 1]


# --- create_extractor_version: kind validation, no pool touch on failure ---

def test_invalid_kind_is_rejected_before_ever_reaching_the_pool():
    class PoolThatMustNotBeCalled:
        async def fetchval(self, *a, **kw):
            raise AssertionError("must not reach the pool on a validation failure")

    with pytest.raises(ValueError, match="kind must be one of"):
        _run(create_extractor_version(
            PoolThatMustNotBeCalled(), name="x", description="", kind="bogus", version="1",
        ))


# --- select_extractor: scope filter, version sort, kind tiebreak ---

def test_no_candidates_returns_none_not_an_error():
    pool = FakePool(rows=[])
    assert _run(select_extractor(pool)) is None


def test_scope_mismatch_excludes_a_candidate_from_selection():
    pool = FakePool(rows=[_row(scope={"repo": ["backend"]})])
    result = _run(select_extractor(pool, current_scope={"repo": ["frontend"]}))
    assert result is None


def test_higher_numeric_version_wins():
    pool = FakePool(rows=[_row(version="1"), _row(version="10"), _row(version="2")])
    result = _run(select_extractor(pool))
    assert result["version"] == "10"  # numeric compare, not lexicographic ("10" < "2" as strings)


def test_non_numeric_version_falls_back_to_zero_without_raising():
    pool = FakePool(rows=[_row(name="a", version="not-a-number"), _row(name="b", version="1")])
    result = _run(select_extractor(pool))
    assert result["name"] == "b"  # the real numeric version beats the unparseable one


def test_non_deterministic_kind_wins_the_tiebreak_at_equal_version():
    pool = FakePool(rows=[
        _row(name="baseline", version="1", kind="deterministic"),
        _row(name="challenger", version="1", kind="llm"),
    ])
    result = _run(select_extractor(pool))
    assert result["name"] == "challenger"


def test_deterministic_still_wins_when_its_version_is_strictly_higher():
    """The kind tiebreak only applies AT EQUAL version -- version stays
    the primary sort key."""
    pool = FakePool(rows=[
        _row(name="baseline", version="2", kind="deterministic"),
        _row(name="challenger", version="1", kind="llm"),
    ])
    result = _run(select_extractor(pool))
    assert result["name"] == "baseline"


def test_current_scope_defaults_to_empty_dict_when_omitted():
    pool = FakePool(rows=[_row(scope={})])
    result = _run(select_extractor(pool))
    assert result is not None


# --- extractor_stats: division-by-zero guards ---

def test_stats_with_no_procedures_produced_reports_none_rates_not_a_crash():
    pool = FakePool(stats_rows=[
        {"total": 0, "approved": 0, "rejected": 0},
        {"successes": 0, "attempts": 0},
    ])
    stats = _run(extractor_stats(pool, extractor_id="e1", name="ext"))
    assert stats["procedures_produced"] == 0
    assert stats["human_approval_rate"] is None
    assert stats["downstream_success_rate"] is None
    assert stats["downstream_attempts"] == 0


def test_stats_computes_real_rates_when_data_exists():
    pool = FakePool(stats_rows=[
        {"total": 4, "approved": 3, "rejected": 1},
        {"successes": 6, "attempts": 8},
    ])
    stats = _run(extractor_stats(pool, extractor_id="e1", name="ext"))
    assert stats["procedures_produced"] == 4
    assert stats["human_approval_rate"] == pytest.approx(0.75)
    assert stats["downstream_success_rate"] == pytest.approx(0.75)


def test_stats_extracted_by_tag_matches_the_stamping_convention():
    pool = FakePool(stats_rows=[
        {"total": 0, "approved": 0, "rejected": 0},
        {"successes": 0, "attempts": 0},
    ])
    stats = _run(extractor_stats(pool, extractor_id="abc-123", name="ext"))
    assert stats["extracted_by"] == "ext@abc-123"
