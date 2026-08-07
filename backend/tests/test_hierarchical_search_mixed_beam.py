"""
Regression tests for a real bug found against production data (not
synthetic): when a beam contains a mix of leaf roots (no children --
already a final answer) and internal roots (have children -- need
further descent), the old code discarded every leaf the moment ANY
beam member had children, even if that leaf scored higher than
anything descent into the internal node went on to find.

Confirmed for real: querying 'api' returned a leaf named 'rag' at
similarity 0.513, while 'api' itself -- sitting in the same beam --
scored 0.6467 and was silently thrown away because a sibling group in
the beam had children and 'api' didn't.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import numpy as np

from app.services.hierarchy import batch_hierarchical_search, hierarchical_search
from app.services.access import AccessScope
from tests.test_subtask_reuse import FakeEmbedder, FakePool


def unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


def build_mixed_beam_graph():
    """
    api: a ROOT with NO children, high query-similarity.
    group: a ROOT WITH children, lower query-similarity than api, whose
    best descendant (leaf_in_group) scores lower still than api.
    Reproduces the exact real-world shape: a leaf that should win sits
    in the same beam as an internal node that shouldn't.
    """
    pool = FakePool()
    D = 16
    query_dir = unit(np.ones(D))

    api_vec = unit(query_dir + 0.05 * np.random.default_rng(1).normal(size=D))  # close to query
    api_id = pool.add_node("task_nodes", "api", api_vec)

    group_vec = unit(query_dir + 0.6 * np.random.default_rng(2).normal(size=D))  # further from query
    group_id = pool.add_node("task_nodes", "group", group_vec)

    leaf_in_group_vec = unit(query_dir + 0.65 * np.random.default_rng(3).normal(size=D))  # even further
    leaf_id = pool.add_node("task_nodes", "rag", leaf_in_group_vec)
    pool.add_parent_of("task_nodes", group_id, leaf_id)

    return pool, api_id, group_id, leaf_id, query_dir


def test_hierarchical_search_does_not_drop_a_higher_scoring_leaf_from_a_mixed_beam():
    pool, api_id, group_id, leaf_id, query_dir = build_mixed_beam_graph()
    query_text = "api "
    embedder = FakeEmbedder({query_text: query_dir})

    result = asyncio.run(hierarchical_search(
        pool, "task_nodes", query_text, scope=AccessScope.unrestricted(),
        embedder=embedder, beam=2, adaptive=False,
    ))

    assert result.leaf_id == api_id, (
        f"expected 'api' ({api_id}) to win -- it scored higher than the group and never had "
        f"children, but got {result.leaf_name!r} instead. This is the exact bug found against "
        f"real production data: a higher-scoring leaf silently dropped because a sibling in "
        f"the same beam had children."
    )
    assert result.leaf_name == "api"


def test_batch_hierarchical_search_does_not_drop_a_higher_scoring_leaf_from_a_mixed_beam():
    pool, api_id, group_id, leaf_id, query_dir = build_mixed_beam_graph()

    results = asyncio.run(batch_hierarchical_search(
        pool, "task_nodes", {"q1": query_dir.tolist()},
        scope=AccessScope.unrestricted(), beam=2, adaptive=False,
    ))

    assert results["q1"].leaf_id == api_id
    assert results["q1"].leaf_name == "api"


def test_true_best_leaf_wins_even_when_found_in_an_earlier_round_than_final_descent():
    """
    Slightly harder version: the winning leaf is found in round 1, but
    the search still needs to descend further for OTHER beam members --
    confirms best_leaf survives being carried across multiple loop
    iterations, not just a single round.
    """
    pool = FakePool()
    D = 16
    query_dir = unit(np.ones(D))

    winner_vec = unit(query_dir + 0.05 * np.random.default_rng(5).normal(size=D))
    winner_id = pool.add_node("task_nodes", "winner", winner_vec)

    # A chain of internal nodes 3 levels deep, all scoring lower than winner
    top_vec = unit(query_dir + 0.5 * np.random.default_rng(6).normal(size=D))
    top_id = pool.add_node("task_nodes", "chain_top", top_vec)
    mid_vec = unit(query_dir + 0.55 * np.random.default_rng(7).normal(size=D))
    mid_id = pool.add_node("task_nodes", "chain_mid", mid_vec)
    bottom_vec = unit(query_dir + 0.6 * np.random.default_rng(8).normal(size=D))
    bottom_id = pool.add_node("task_nodes", "chain_bottom", bottom_vec)
    pool.add_parent_of("task_nodes", top_id, mid_id)
    pool.add_parent_of("task_nodes", mid_id, bottom_id)

    query_text = "winner query "
    embedder = FakeEmbedder({query_text: query_dir})

    result = asyncio.run(hierarchical_search(
        pool, "task_nodes", query_text, scope=AccessScope.unrestricted(),
        embedder=embedder, beam=2, adaptive=False,
    ))

    assert result.leaf_id == winner_id, (
        f"expected 'winner' to survive across multiple rounds of descent into the chain, "
        f"got {result.leaf_name!r} instead"
    )
