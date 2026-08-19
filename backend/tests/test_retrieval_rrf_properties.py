"""
Property-based tests for app/services/retrieval.py's RRF fusion (ticket
14, memory-substrate map). This module had ZERO test coverage before
this file -- ticket 14's own resolved answer states explicitly:
"Property-based tests land before any extension of retrieval.py. The
component is load-bearing with zero coverage." This is that
prerequisite, done before any new retrieval-hierarchy code is built on
top of it.

Characterization tests pin output stability and nothing about
correctness -- for a RANKING function there is no obviously "right"
answer to assert. What IS assertable without knowing the right answer
are invariants, and ticket 14 names four concrete ones: monotonicity,
idempotence, commutativity, stability. Each is tested here directly
against fuse_rrf() (the pure arithmetic extracted from retrieve()'s body
specifically so it's testable without a live database).

No hypothesis dependency -- not used anywhere else in this codebase, and
adding one for a single test file is a real cost weighed against a
manual alternative. Property checks below use a fixed-seed
pseudorandom generator instead: many varied, reproducible input
configurations checked against each invariant in a loop, same spirit as
a property-based test, without the new dependency.
"""
import random
from uuid import uuid4

from app.services.retrieval import RRF_K, fuse_rrf

N_TRIALS = 200  # per invariant -- enough to catch a real violation
# without making the suite slow; each trial is cheap pure arithmetic.


def _random_ranked_list(rng: random.Random, ids: list, table: str, max_len: int) -> list:
    """A real, varied (id, table, rank) list: a random subset of `ids`,
    shuffled, ranks assigned by position."""
    chosen = rng.sample(ids, k=rng.randint(0, min(max_len, len(ids))))
    rng.shuffle(chosen)
    return [(node_id, table, rank) for rank, node_id in enumerate(chosen)]


def _random_ranked_lists(rng: random.Random, n_lists: int = 3, n_ids: int = 12):
    ids = [uuid4() for _ in range(n_ids)]
    table = "task_nodes"
    return [
        (_random_ranked_list(rng, ids, table, max_len=n_ids), f"signal_{i}")
        for i in range(n_lists)
    ], ids


def test_idempotence_same_inputs_produce_identical_output():
    """Calling fuse_rrf() twice with the exact same inputs must produce
    the exact same scores -- no hidden mutable state, no ordering
    dependency on prior calls."""
    rng = random.Random(1)
    for trial in range(N_TRIALS):
        lists, _ = _random_ranked_lists(rng, n_lists=rng.randint(1, 4))
        scores1, matched1 = fuse_rrf(lists)
        scores2, matched2 = fuse_rrf(lists)
        assert scores1 == scores2, f"trial {trial}: fuse_rrf() is not deterministic"
        assert matched1 == matched2, f"trial {trial}: matched labels are not deterministic"


def test_commutativity_input_list_order_does_not_affect_scores():
    """Ticket 14.8: 'commutativity (input list order irrelevant under
    symmetric weights)'. fuse_rrf() sums contributions across lists, so
    which order the (hits, label) pairs are passed in must not change
    any item's final fused score -- only which list an item appeared in
    matters, not the list's position in the call.

    REAL FINDING, not a test bug: exact (`==`) equality is too strict
    here. IEEE 754 float addition is not associative -- summing the same
    set of 1/(k+rank+1) terms in a different order can differ in the
    last bit (confirmed directly: a real trial produced
    0.06283246339995907 vs ...908, an ~1e-17 relative difference).
    That's correct floating-point behaviour, not a defect in fuse_rrf,
    so the property under test is commutativity up to float tolerance,
    not bit-exact equality -- checked with math.isclose's default
    relative tolerance (1e-9), many orders of magnitude looser than the
    ~1e-16 machine-epsilon-scale differences summation order actually
    produces, so this still catches a REAL commutativity violation
    (e.g. a label-dependent weight) while not failing on float noise.
    """
    import math

    rng = random.Random(2)
    for trial in range(N_TRIALS):
        lists, _ = _random_ranked_lists(rng, n_lists=rng.randint(2, 5))
        scores_original, _ = fuse_rrf(lists)
        shuffled = list(lists)
        rng.shuffle(shuffled)
        scores_shuffled, _ = fuse_rrf(shuffled)
        assert scores_original.keys() == scores_shuffled.keys(), (
            f"trial {trial}: reordering the input lists changed WHICH items scored"
        )
        for key in scores_original:
            assert math.isclose(scores_original[key], scores_shuffled[key], rel_tol=1e-9), (
                f"trial {trial}: reordering the input lists changed {key}'s fused score "
                f"beyond float noise ({scores_original[key]} vs {scores_shuffled[key]})"
            )


def test_monotonicity_a_better_rank_never_decreases_fused_score():
    """Ticket 14.8: 'monotonicity (higher fused score => higher rank)'.
    Tested via the underlying mechanism: moving one item to a STRICTLY
    BETTER (lower-numbered) rank within one input list, with every other
    item's rank in that list held fixed relative to the moved item,
    must not decrease that item's own fused score (RRF's 1/(k+rank+1)
    term is itself strictly decreasing in rank, so a lower rank number
    can only raise or hold a term, never lower it)."""
    rng = random.Random(3)
    for trial in range(N_TRIALS):
        n_ids = rng.randint(3, 10)
        ids = [uuid4() for _ in range(n_ids)]
        table = "task_nodes"
        base_order = list(ids)
        rng.shuffle(base_order)
        base_list = [(nid, table, rank) for rank, nid in enumerate(base_order)]

        target = rng.choice(ids)
        target_rank = next(r for nid, _, r in base_list if nid == target)
        if target_rank == 0:
            continue  # already best possible rank, nothing to improve
        improved_rank = rng.randint(0, target_rank - 1)

        # Build an "improved" list: same items, target moved to a
        # strictly better rank, everything else shifted down by one to
        # keep ranks contiguous (a real, valid re-ranking, not a
        # fabricated rank collision).
        remaining = [nid for nid in base_order if nid != target]
        improved_order = remaining[:improved_rank] + [target] + remaining[improved_rank:]
        improved_list = [(nid, table, rank) for rank, nid in enumerate(improved_order)]

        scores_base, _ = fuse_rrf([(base_list, "s")])
        scores_improved, _ = fuse_rrf([(improved_list, "s")])
        key = (target, table)
        assert scores_improved[key] >= scores_base[key], (
            f"trial {trial}: improving {target}'s rank from {target_rank} to "
            f"{improved_rank} decreased its fused score "
            f"({scores_base[key]} -> {scores_improved[key]})"
        )


def test_stability_appending_a_new_item_does_not_reorder_existing_items():
    """Ticket 14.8: 'stability (small input perturbations don't reorder
    the head)'. Appending one new item at the WORST rank of one input
    list (a small, realistic perturbation -- one more low-relevance
    candidate entering the pool) must not change the RELATIVE order of
    any pair of previously-present items' fused scores."""
    rng = random.Random(4)
    for trial in range(N_TRIALS):
        lists, ids = _random_ranked_lists(rng, n_lists=rng.randint(1, 4), n_ids=10)
        scores_before, _ = fuse_rrf(lists)

        # Perturb: append a brand-new id at the end (worst rank) of one
        # randomly chosen list.
        new_id = uuid4()
        list_idx = rng.randrange(len(lists))
        hits, label = lists[list_idx]
        perturbed_hits = hits + [(new_id, "task_nodes", len(hits))]
        perturbed_lists = list(lists)
        perturbed_lists[list_idx] = (perturbed_hits, label)

        scores_after, _ = fuse_rrf(perturbed_lists)

        # Every pairwise ORDER among previously-present items must be
        # unchanged -- the new item may join the ranking, but it must
        # not reshuffle who was ahead of whom before it arrived.
        common_keys = [k for k in scores_before if k in scores_after]
        for i in range(len(common_keys)):
            for j in range(i + 1, len(common_keys)):
                a, b = common_keys[i], common_keys[j]
                before_cmp = (scores_before[a] > scores_before[b]) - (scores_before[a] < scores_before[b])
                after_cmp = (scores_after[a] > scores_after[b]) - (scores_after[a] < scores_after[b])
                assert before_cmp == after_cmp, (
                    f"trial {trial}: appending a new low-relevance item reordered "
                    f"two previously-present items ({a} vs {b})"
                )


def test_empty_input_produces_empty_output():
    scores, matched = fuse_rrf([])
    assert scores == {}
    assert matched == {}


def test_an_item_present_in_multiple_lists_scores_higher_than_appearing_in_one():
    """Basic sanity check underlying the whole fusion premise: an item
    both signals agree on should outrank one only one signal found, all
    else equal."""
    a, b = uuid4(), uuid4()
    table = "task_nodes"
    list1 = [(a, table, 0), (b, table, 1)]
    list2 = [(a, table, 0)]
    scores, _ = fuse_rrf([(list1, "semantic"), (list2, "keyword")])
    assert scores[(a, table)] > scores[(b, table)]


def test_matched_by_records_every_contributing_signal():
    a = uuid4()
    table = "task_nodes"
    _, matched = fuse_rrf([
        ([(a, table, 0)], "semantic"),
        ([(a, table, 2)], "keyword"),
    ])
    assert matched[(a, table)] == ["semantic", "keyword"]


def test_default_k_matches_module_constant():
    """fuse_rrf()'s default k must stay in sync with RRF_K -- a caller
    relying on the default (retrieve() does) must get the same constant
    the module documents as its RRF smoothing factor."""
    a = uuid4()
    table = "task_nodes"
    scores_default, _ = fuse_rrf([([(a, table, 0)], "s")])
    scores_explicit, _ = fuse_rrf([([(a, table, 0)], "s")], k=RRF_K)
    assert scores_default == scores_explicit
