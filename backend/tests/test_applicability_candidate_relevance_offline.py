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

        # B37's real coarse_route_safe_exclusions wiring (applicability.py
        # ::_fetch_candidate_pool) queries hierarchy.py::_fetch_roots
        # whenever goal_text is given -- honestly no real hierarchy exists
        # in this fake corpus, matching the real function's own "fewer
        # than 2 real roots -> no-op" contract.
        if "FROM procedures n WHERE n.t_invalid IS NULL" in normalized and "has_embedding" in normalized:
            return []

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


@pytest.mark.asyncio
async def test_no_embedding_path_ignores_goal_text_no_extra_roundtrip():
    """goal_text alone (no embedding) must NOT add a lexical query -- the
    pinned no-embedding single-query contract is unchanged."""
    pool = OrderAwareFakePool(ALL_PROCEDURES)
    await _fetch_candidate_pool(
        pool, goal_embedding=None, candidate_pool_size=2,
        goal_text="rotate the deployment credentials",
    )
    assert len(pool.fetch_calls) == 1


# --- S5: lexical candidate leg over the canonical retrieval_document -----

class LexAwareFakePool(OrderAwareFakePool):
    """Adds a handler for the Part-5 lexical leg: rows whose 'text' field
    shares a word with the query rank first, in configured order."""

    def __init__(self, procedures, lexical_hits):
        super().__init__(procedures)
        self._lexical_hits = lexical_hits  # ordered list of ids

    async def fetch(self, sql, *params):
        normalized = " ".join(sql.split())
        if "to_tsvector('english', coalesce(retrieval_document" in normalized:
            self.fetch_calls.append("LEXICAL")
            limit = params[-1]
            return [{"id": i} for i in self._lexical_hits[:limit]]
        return await super().fetch(sql, *params)


# A vocab-mismatch procedure: the vector puts it just inside the pool
# (similarity_rank 3) and it is NOT among the cheapest, so cost+similarity
# alone leave it on the bubble; the lexical leg is what secures it.
LEX_RELEVANT = _procedure("lex-relevant", n_preconditions=4, similarity_rank=3)
LEX_POOL = [LEX_RELEVANT] + CHEAP_BUT_IRRELEVANT


@pytest.mark.asyncio
async def test_lexical_leg_fires_only_with_embedding_and_goal_text():
    pool = LexAwareFakePool(LEX_POOL, lexical_hits=["lex-relevant"])
    await _fetch_candidate_pool(
        pool, goal_embedding=[0.1] * 1024, candidate_pool_size=4,
        goal_text="isolate parallel agents with git worktrees",
    )
    assert "LEXICAL" in pool.fetch_calls, "lexical leg must run when both signals are present"

    pool2 = LexAwareFakePool(LEX_POOL, lexical_hits=["lex-relevant"])
    await _fetch_candidate_pool(
        pool2, goal_embedding=[0.1] * 1024, candidate_pool_size=4, goal_text=None,
    )
    assert "LEXICAL" not in pool2.fetch_calls, "no goal_text -> no lexical leg"


@pytest.mark.asyncio
async def test_lexical_hit_lifts_a_bubble_vocab_match_over_a_weaker_row():
    """RRF over cost + similarity + lexical (no new formula). A procedure
    on the candidate-pool bubble that is ALSO the clear lexical match must
    rank above one that only barely made a single list."""
    with_lex = LexAwareFakePool(LEX_POOL, lexical_hits=["lex-relevant"])
    ranked_with = await _fetch_candidate_pool(
        with_lex, goal_embedding=[0.1] * 1024, candidate_pool_size=4,
        goal_text="isolate parallel agents with git worktrees",
    )
    without_lex = LexAwareFakePool(LEX_POOL, lexical_hits=[])
    ranked_without = await _fetch_candidate_pool(
        without_lex, goal_embedding=[0.1] * 1024, candidate_pool_size=4,
        goal_text="isolate parallel agents with git worktrees",
    )
    pos_with = [r["id"] for r in ranked_with].index("lex-relevant")
    pos_without = [r["id"] for r in ranked_without].index("lex-relevant")
    assert pos_with < pos_without, (
        "the lexical leg must improve the vocab-match procedure's rank, "
        f"got {pos_with} with vs {pos_without} without"
    )
