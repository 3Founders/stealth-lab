"""
Proving test for the candidate-selection relevance fix in
applicability.py::_fetch_candidate_pool.

THE BUG (fixed this pass): the candidate pre-filter used to rank
exclusively by `jsonb_array_length(preconditions) ASC` -- cheapest to
match first -- with zero embedding term. At a tight candidate_pool_size,
a genuinely relevant procedure with many real preconditions could never
even reach the hard-constraint cascade, regardless of how well it matched
the goal. This is independent of corpus size: it's wrong at 5 rows, not
just at scale.

Unlike test_applicability_cascade_offline.py's shared FakePool (which
ignores ORDER BY/LIMIT entirely and always returns every configured row --
fine for its own cache-count purpose, wrong for proving THIS bug), this
file's fake actually respects ordering and LIMIT per query shape, because
the bug lives exactly in which rows survive that LIMIT.
"""
import asyncio

import pytest

from app.services.applicability import _fetch_candidate_pool


def _procedure(proc_id, n_preconditions, similarity_rank):
    return {
        "id": proc_id,
        "n_preconditions": n_preconditions,
        "similarity_rank": similarity_rank,  # lower = more similar
    }


# The realistic shape this bug actually hit: one genuinely relevant
# procedure buried under real preconditions (rank last on cost), several
# cheap-but-irrelevant ones (rank first on cost, last on relevance).
RELEVANT_BUT_COSTLY = _procedure("relevant", n_preconditions=10, similarity_rank=0)
CHEAP_BUT_IRRELEVANT = [
    _procedure(f"irrelevant-{i}", n_preconditions=0, similarity_rank=10 + i)
    for i in range(5)
]
ALL_PROCEDURES = [RELEVANT_BUT_COSTLY] + CHEAP_BUT_IRRELEVANT


class OrderAwareFakePool:
    """Actually respects ORDER BY + LIMIT per query shape, unlike the
    shared cascade FakePool -- required to prove a bug that lives
    entirely in what a LIMIT clause admits."""

    def __init__(self, procedures):
        self._procedures = {p["id"]: p for p in procedures}
        self.fetch_calls: list[str] = []

    async def fetch(self, sql, *params):
        normalized = " ".join(sql.split())
        self.fetch_calls.append(normalized)

        if "id = ANY" in normalized:
            ids = params[-1]
            return [dict(self._procedures[i]) for i in ids if i in self._procedures]

        if "ORDER BY embedding" in normalized:
            limit = params[-1]
            ranked = sorted(self._procedures.values(), key=lambda p: p["similarity_rank"])
            return [{"id": p["id"]} for p in ranked[:limit]]

        if "ORDER BY jsonb_array_length" in normalized:
            limit = params[-1]
            ranked = sorted(self._procedures.values(), key=lambda p: p["n_preconditions"])
            return [{"id": p["id"]} for p in ranked[:limit]]

        raise AssertionError(f"unexpected query shape: {normalized}")


@pytest.mark.asyncio
async def test_cost_only_ordering_excludes_the_relevant_procedure():
    """Pins the BUG as it existed, using the exact same fake -- proves
    this test setup genuinely reproduces the failure, not just the fix."""
    pool = OrderAwareFakePool(ALL_PROCEDURES)
    rows = await _fetch_candidate_pool(pool, goal_embedding=None, candidate_pool_size=2)
    ids = [r["id"] for r in rows]
    assert "relevant" not in ids, (
        "sanity check on the fake itself: with no embedding, cost-only "
        "ordering must reproduce the original bug (relevant procedure "
        "excluded by its own precondition count)"
    )


@pytest.mark.asyncio
async def test_fused_ordering_admits_the_relevant_procedure():
    """THE FIX: with a goal_embedding supplied, the same tight
    candidate_pool_size=2 must now surface the relevant procedure --
    fused via RRF against the cost-only ranking, not replacing it."""
    pool = OrderAwareFakePool(ALL_PROCEDURES)
    rows = await _fetch_candidate_pool(
        pool, goal_embedding=[0.1] * 1024, candidate_pool_size=2,
    )
    ids = [r["id"] for r in rows]
    assert "relevant" in ids, (
        "FAIL: the relevant procedure is still excluded -- the fix did "
        "not actually change which candidates survive the pre-filter"
    )


@pytest.mark.asyncio
async def test_no_embedding_path_is_untouched_single_query():
    """The other half of the contract: a caller with no embedding must
    see exactly the old query shape, not a silently-added round trip."""
    pool = OrderAwareFakePool(ALL_PROCEDURES)
    await _fetch_candidate_pool(pool, goal_embedding=None, candidate_pool_size=2)
    assert len(pool.fetch_calls) == 1, (
        "no-embedding path must stay a single query -- got "
        f"{len(pool.fetch_calls)}: {pool.fetch_calls}"
    )
