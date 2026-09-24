"""Offline proving tests for pure hierarchical Goal candidate routing."""
from __future__ import annotations

from app.services.hierarchical_goal_routing import (
    DEFAULT_MAX_FANOUT,
    DEFAULT_MAX_GRAPH_CANDIDATES,
    DEFAULT_MAX_HOPS,
    HierarchicalGoalRoutingConfig,
    route_hierarchical_goal_candidates,
    traverse_accepted_goal_relations,
)


def _anchor(goal_id: str, score: float = 0.0) -> dict:
    return {
        "goal_id": goal_id,
        "canonical_name": goal_id.replace("-", " "),
        "score": score,
    }


def _edge(
    specific: str,
    abstract: str,
    *,
    edge_id: str,
    status: str = "accepted",
    relation_type: str = "SPECIALIZES",
    **extra,
) -> dict:
    row = {
        "id": edge_id,
        "specific_goal_id": specific,
        "abstract_goal_id": abstract,
        "relation_type": relation_type,
        "status": status,
        "provenance": "accepted_test_edge",
        "decision_id": f"decision-{edge_id}",
    }
    row.update(extra)
    return row


def test_expands_parents_and_children_after_preserving_anchor_order():
    result = route_hierarchical_goal_candidates(
        [_anchor("anchor-a", 0.8), _anchor("anchor-b", 0.2)],
        [
            _edge("anchor-b", "child-b", edge_id="child-b-edge"),
            _edge("anchor-a", "parent-a", edge_id="parent-a-edge"),
        ],
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "anchor-a",
        "anchor-b",
        "parent-a",
        "child-b",
    ]
    assert [candidate.origin for candidate in result.semantic_anchors] == [
        "semantic_anchor",
        "semantic_anchor",
    ]
    assert [candidate.origin for candidate in result.graph_candidates] == [
        "graph",
        "graph",
    ]
    assert result.diagnostics.mode == "hierarchical"
    assert result.diagnostics.used_flat_fallback is False


def test_multi_parent_child_is_deduplicated_with_all_truthful_paths():
    result = route_hierarchical_goal_candidates(
        [_anchor("parent-a"), _anchor("parent-b")],
        [
            _edge("shared-child", "parent-a", edge_id="edge-a"),
            _edge("shared-child", "parent-b", edge_id="edge-b"),
        ],
        config=HierarchicalGoalRoutingConfig(expand_parents=False),
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "parent-a",
        "parent-b",
        "shared-child",
    ]
    child = result.graph_candidates[0]
    assert child.abstraction_path in {
        ("parent-a", "shared-child"),
        ("parent-b", "shared-child"),
    }
    assert {item.edge_id for item in child.relation_provenance} == {
        "edge-a",
        "edge-b",
    }
    assert {item.abstraction_path for item in child.relation_provenance} == {
        ("parent-a", "shared-child"),
        ("parent-b", "shared-child"),
    }


def test_only_accepted_specializes_edges_can_expand_or_prove_a_route():
    result = route_hierarchical_goal_candidates(
        [_anchor("anchor")],
        [
            _edge("accepted-child", "anchor", edge_id="accepted"),
            _edge("proposed-child", "anchor", edge_id="proposed", status="proposed"),
            _edge("rejected-child", "anchor", edge_id="rejected", status="rejected"),
            _edge("other-child", "anchor", edge_id="other", relation_type="RELATED"),
        ],
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "anchor",
        "accepted-child",
    ]
    used_edges = {
        item.edge_id
        for candidate in result.graph_candidates
        for item in candidate.relation_provenance
    }
    assert used_edges == {"accepted"}
    assert result.diagnostics.ignored_relation_statuses == {
        "proposed": 1,
        "rejected": 1,
    }
    assert result.diagnostics.ignored_relation_types == {"RELATED": 1}


def test_hop_and_fanout_bounds_are_enforced_and_disclosed():
    result = route_hierarchical_goal_candidates(
        [_anchor("parent")],
        [
            _edge("child-1", "parent", edge_id="child-1"),
            _edge("child-2", "parent", edge_id="child-2"),
        ],
        config=HierarchicalGoalRoutingConfig(
            max_hops=1,
            max_fanout=1,
            expand_parents=False,
        ),
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "parent",
        "child-1",
    ]
    assert result.diagnostics.truncated is True
    assert result.diagnostics.degraded is True
    assert "fanout_limit_reached" in result.diagnostics.degraded_reasons


def test_hop_cap_stops_grandchildren_instead_of_building_a_closure():
    result = route_hierarchical_goal_candidates(
        [_anchor("parent")],
        [
            _edge("child", "parent", edge_id="child"),
            _edge("grandchild", "child", edge_id="grandchild"),
        ],
        config=HierarchicalGoalRoutingConfig(
            max_hops=1,
            expand_parents=False,
        ),
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "parent",
        "child",
    ]
    assert "hop_limit_reached" in result.diagnostics.degraded_reasons
    assert result.diagnostics.truncated is True


def test_flat_result_returns_anchor_candidates_with_explicit_fallback():
    anchors = [_anchor("only-anchor", 0.0)]
    result = route_hierarchical_goal_candidates(
        anchors,
        [_edge("child", "unrelated", edge_id="unrelated")],
    )

    assert result.candidates[0].candidate == anchors[0]
    assert result.graph_candidates == ()
    assert result.diagnostics.mode == "flat"
    assert result.diagnostics.used_flat_fallback is True
    assert result.diagnostics.fallback_reasons == (
        "no_relevant_accepted_relations",
    )
    assert result.diagnostics.degraded is False


def test_abstraction_paths_only_contain_real_accepted_edges():
    result = route_hierarchical_goal_candidates(
        [_anchor("ancestor")],
        [
            _edge("child", "ancestor", edge_id="first"),
            _edge("grandchild", "child", edge_id="second"),
            _edge("unrelated", "other-anchor", edge_id="unrelated"),
        ],
        config=HierarchicalGoalRoutingConfig(
            max_hops=2,
            expand_parents=False,
        ),
    )

    grandchild = next(
        candidate
        for candidate in result.graph_candidates
        if candidate.goal_id == "grandchild"
    )
    assert grandchild.abstraction_path == ("ancestor", "child", "grandchild")
    assert [item.edge_id for item in grandchild.relation_provenance] == [
        "first",
        "second",
    ]
    for hop, item in enumerate(grandchild.relation_provenance, start=1):
        assert item.abstraction_path == ("ancestor", "child", "grandchild")[: hop + 1]
        assert item.abstraction_path[-2:] == (item.from_goal_id, item.to_goal_id)
        assert item.hop == hop
        assert item.status == "accepted"
        assert item.relation_type == "SPECIALIZES"
    assert "unrelated" not in [candidate.goal_id for candidate in result.candidates]


def test_pure_multi_hop_traversal_utility_keeps_paths_and_does_not_mutate_edges():
    edges = [
        _edge("child", "anchor", edge_id="first"),
        _edge("grandchild", "child", edge_id="second"),
    ]
    before = [dict(edge) for edge in edges]

    traversal = traverse_accepted_goal_relations(
        ["anchor"],
        edges,
        direction="children",
        config=HierarchicalGoalRoutingConfig(
            max_hops=2,
            expand_parents=False,
        ),
    )

    assert edges == before
    assert [path.goal_id for path in traversal.paths] == ["child", "grandchild"]
    assert [path.abstraction_path for path in traversal.paths] == [
        ("anchor", "child"),
        ("anchor", "child", "grandchild"),
    ]
    assert all(path.depth == len(path.abstraction_path) - 1 for path in traversal.paths)


def test_semantic_anchor_zero_score_and_graph_zero_score_are_preserved():
    records = {
        "parent": {"goal_id": "parent", "canonical_name": "Parent", "score": 0.0},
        "child": {"goal_id": "child", "canonical_name": "Child", "score": 0.0},
    }
    result = route_hierarchical_goal_candidates(
        [_anchor("parent", 0.0)],
        [_edge("child", "parent", edge_id="edge")],
        goal_records=records,
        config=HierarchicalGoalRoutingConfig(expand_parents=False),
    )

    assert result.semantic_anchors[0].candidate["score"] == 0.0
    assert result.graph_candidates[0].candidate["score"] == 0.0
    assert [candidate.score for candidate in result.candidates] == [0.0, 0.0]


def test_goal_that_is_both_anchor_and_graph_neighbor_stays_only_an_anchor():
    result = route_hierarchical_goal_candidates(
        [_anchor("shared")],
        [
            _edge("shared", "parent", edge_id="outgoing"),
            _edge("child", "shared", edge_id="incoming"),
        ],
    )

    assert [candidate.goal_id for candidate in result.candidates] == [
        "shared",
        "parent",
        "child",
    ]
    assert result.semantic_anchors[0].abstraction_path == ("shared",)
    assert result.semantic_anchors[0].relation_provenance == ()
    assert "shared" not in [candidate.goal_id for candidate in result.graph_candidates]


def test_defaults_are_one_hop_with_bounded_fanout_and_candidates():
    assert HierarchicalGoalRoutingConfig() == HierarchicalGoalRoutingConfig(
        max_hops=DEFAULT_MAX_HOPS,
        max_fanout=DEFAULT_MAX_FANOUT,
        max_graph_candidates=DEFAULT_MAX_GRAPH_CANDIDATES,
        expand_parents=True,
        expand_children=True,
    )
    assert DEFAULT_MAX_HOPS == 1
    assert DEFAULT_MAX_FANOUT > 0
    assert DEFAULT_MAX_GRAPH_CANDIDATES > 0
