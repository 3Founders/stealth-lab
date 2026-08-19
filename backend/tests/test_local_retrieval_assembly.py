"""
Pure unit tests for app/services/local_retrieval.py's token-budget
assembly (ticket 14). No DB needed -- assemble_context() and its helpers
are pure functions over RetrievedNode lists.
"""
from uuid import uuid4

from app.services.local_retrieval import (
    DEFAULT_TOKEN_BUDGET,
    TIER_PRIORITY,
    StructuralContext,
    _estimate_tokens,
    _path_matches,
    assemble_context,
)
from app.services.retrieval import RetrievedNode


def _node(name="n", description=None, score=1.0, hops=0):
    return RetrievedNode(
        id=uuid4(), table="task_nodes", name=name, description=description,
        score=score, matched_by=["semantic"], hops=hops,
    )


def test_tier_priority_order_is_structural_temporal_causal_semantic():
    """Ticket 14's resolved answer, verbatim: 'filled in priority order
    structural > temporal > causal > semantic.'"""
    assert TIER_PRIORITY == ("structural", "temporal", "causal", "semantic")


def test_higher_priority_tier_is_included_before_lower_priority_tier():
    structural_node = _node("structural-node")
    semantic_node = _node("semantic-node")
    # Budget just large enough for ONE node -- forces a real choice
    # between tiers, not a "both fit anyway" false pass.
    one_line_tokens = _estimate_tokens(
        f"[task:{structural_node.id}] structural-node"
    )
    result = assemble_context(
        [("semantic", [semantic_node]), ("structural", [structural_node])],
        token_budget=one_line_tokens,
    )
    assert structural_node.id in result.included_node_ids
    assert semantic_node.id not in result.included_node_ids
    assert semantic_node.id in result.excluded_node_ids


def test_budget_truncates_rather_than_degrading_silently():
    nodes = [_node(f"node-{i}") for i in range(20)]
    result = assemble_context([("semantic", nodes)], token_budget=50)
    assert result.estimated_tokens <= 50
    assert len(result.included_node_ids) < len(nodes), "a 50-token budget must not fit all 20 nodes"
    assert len(result.excluded_node_ids) > 0


def test_a_node_present_in_two_tiers_is_included_once_counted_to_the_higher_tier():
    shared = _node("shared-node")
    result = assemble_context(
        [("structural", [shared]), ("semantic", [shared])],
        token_budget=DEFAULT_TOKEN_BUDGET,
    )
    assert result.included_node_ids.count(shared.id) == 1
    assert result.tiers_included.get("structural") == 1
    assert result.tiers_included.get("semantic", 0) == 0


def test_unrecognized_tier_name_is_not_silently_dropped():
    """A caller experimenting with a new tier should not lose its
    output for not having updated TIER_PRIORITY."""
    node = _node("custom-tier-node")
    result = assemble_context([("some_new_tier", [node])], token_budget=DEFAULT_TOKEN_BUDGET)
    assert node.id in result.included_node_ids
    assert result.tiers_included.get("some_new_tier") == 1


def test_empty_tiers_produce_empty_context_not_an_error():
    result = assemble_context([], token_budget=DEFAULT_TOKEN_BUDGET)
    assert result.text == ""
    assert result.included_node_ids == []
    assert result.estimated_tokens == 0


def test_token_budget_is_recorded_on_the_result():
    result = assemble_context([], token_budget=1234)
    assert result.token_budget == 1234


def test_path_matches_substring_in_name():
    assert _path_matches("fix in app/services/foo.py", None, ["app/services/foo.py"])


def test_path_matches_substring_in_description():
    assert _path_matches("some node", "touches app/services/bar.py", ["app/services/bar.py"])


def test_path_matches_false_when_no_candidate_present():
    assert not _path_matches("unrelated node", "unrelated description", ["app/services/baz.py"])


def test_path_matches_false_for_empty_candidates():
    assert not _path_matches("anything", "anything", [])


def test_structural_context_has_any_filter():
    assert not StructuralContext().has_any_filter()
    assert StructuralContext(open_files=["a.py"]).has_any_filter()
    assert StructuralContext(relevant_symbols=["foo"]).has_any_filter()


def test_structural_context_has_any_rank_boost():
    assert not StructuralContext().has_any_rank_boost()
    assert StructuralContext(call_graph_ranked_names=["foo"]).has_any_rank_boost()
    assert StructuralContext(recent_commit_files=["a.py"]).has_any_rank_boost()
