"""
Tests for the pure, DB-free half of Part B (app/services/hierarchy.py):
group_with_branching_limit / plan_next_level.

Everything DB-backed (build_hierarchy_for_table, hierarchical_search,
attach_new_leaf) needs a live Postgres+pgvector instance and is covered
by integration_check_v2_hierarchy.py instead, matching this project's
existing split between unit tests and integration_check_*.py scripts.
"""
from __future__ import annotations

from app.services.hierarchy import group_with_branching_limit, plan_next_level


def _sim_from_dict(pairs: dict) -> callable:
    def sim(a, b):
        if a == b:
            return 1.0
        return pairs.get((a, b)) or pairs.get((b, a)) or 0.0
    return sim


def test_every_key_appears_exactly_once():
    keys = [f"k{i}" for i in range(10)]
    sim = _sim_from_dict({})  # nothing similar to anything
    groups = group_with_branching_limit(keys, sim, threshold=0.9)
    seen = [k for g in groups for k in g]
    assert sorted(seen) == sorted(keys)


def test_tight_group_under_max_children_stays_together():
    keys = ["a", "b", "c"]
    sim = _sim_from_dict({("a", "b"): 0.95, ("b", "c"): 0.95, ("a", "c"): 0.95})
    groups = group_with_branching_limit(keys, sim, threshold=0.9, max_children=12)
    assert len(groups) == 1
    assert set(groups[0]) == {"a", "b", "c"}


def test_oversized_group_gets_split_not_left_unbounded():
    """
    20 mutually-similar keys, but max_children=5. A single 20-member
    group would just be flat search wearing a tree costume -- every
    resulting group must respect the cap.
    """
    keys = [f"k{i}" for i in range(20)]
    sim = _sim_from_dict({})

    def uniform_sim(a, b):
        return 1.0 if a == b else 0.92  # all mutually close

    groups = group_with_branching_limit(keys, uniform_sim, threshold=0.9, max_children=5)
    assert all(len(g) <= 5 for g in groups), f"a group exceeded max_children: {[len(g) for g in groups]}"
    seen = [k for g in groups for k in g]
    assert sorted(seen) == sorted(keys)


def test_plan_next_level_marks_singletons_as_not_internal():
    keys = ["solo1", "solo2", "pair_a", "pair_b"]
    sim = _sim_from_dict({("pair_a", "pair_b"): 0.95})
    plan = plan_next_level(keys, sim, threshold=0.9, min_children=2)

    internal = [g for g in plan if g.is_internal]
    leftover = [g for g in plan if not g.is_internal]

    assert len(internal) == 1
    assert set(internal[0].member_keys) == {"pair_a", "pair_b"}
    # solo1 and solo2 never matched anything -- each stays its own
    # non-internal group, not forced into a fake pairing.
    assert sorted(k for g in leftover for k in g.member_keys) == ["solo1", "solo2"]


def test_chain_does_not_get_merged_into_one_giant_group():
    """
    Same chaining scenario Part A tested (dedup.py), reused here because
    Part B's grouping reuses the exact same complete_linkage_clusters
    primitive -- the guarantee should hold identically.
    """
    sims = {
        ("A", "B"): 0.95, ("B", "C"): 0.95, ("C", "D"): 0.95,
        ("A", "D"): 0.60,
    }
    sim = _sim_from_dict(sims)
    groups = group_with_branching_limit(["A", "B", "C", "D"], sim, threshold=0.9)
    for g in groups:
        assert not ("A" in g and "D" in g)


def test_nothing_similar_enough_yields_all_singletons():
    keys = ["x", "y", "z"]
    sim = _sim_from_dict({})  # everything scores 0
    plan = plan_next_level(keys, sim, threshold=0.9, min_children=2)
    assert all(not g.is_internal for g in plan)
    assert len(plan) == 3
