"""
Unit tests for app/services/relevance_gate.py.

The gate is a FILTER, not a score: it drops weak matches, never re-ranks,
and is allowed to return zero. When its cutoff has not been measured it
fails OPEN (inert) rather than guessing a threshold.
"""
from __future__ import annotations

import app.services.relevance_gate as rg


def _reload_with(min_sim, strong_sim):
    """Temporarily set the measured params for a test."""
    old = (rg.RELEVANCE_GATE_MIN_SIMILARITY, rg.RELEVANCE_LABEL_STRONG_SIMILARITY)
    rg.RELEVANCE_GATE_MIN_SIMILARITY = min_sim
    rg.RELEVANCE_LABEL_STRONG_SIMILARITY = strong_sim
    return old


def _restore(old):
    rg.RELEVANCE_GATE_MIN_SIMILARITY, rg.RELEVANCE_LABEL_STRONG_SIMILARITY = old


def test_fails_open_when_cutoff_unmeasured():
    old = _reload_with(None, None)
    try:
        hits = [{"similarity_score": 0.01}, {"similarity_score": None}, {"similarity_score": 0.99}]
        assert rg.apply_relevance_gate(hits) == hits  # nothing dropped
        assert rg.passes_relevance_gate(0.0) is True
        assert rg.relevance_label(0.5) is None
    finally:
        _restore(old)


def test_drops_below_cutoff_keeps_at_or_above_order_preserved():
    old = _reload_with(0.40, 0.65)
    try:
        hits = [
            {"id": "a", "similarity_score": 0.72},
            {"id": "b", "similarity_score": 0.41},
            {"id": "c", "similarity_score": 0.39},
            {"id": "d", "similarity_score": 0.10},
        ]
        kept = rg.apply_relevance_gate(hits)
        assert [h["id"] for h in kept] == ["a", "b"]  # order preserved, weak ones gone
    finally:
        _restore(old)


def test_zero_results_is_a_valid_outcome():
    old = _reload_with(0.60, 0.80)
    try:
        hits = [{"similarity_score": 0.2}, {"similarity_score": 0.55}]
        assert rg.apply_relevance_gate(hits) == []
    finally:
        _restore(old)


def test_none_similarity_survivor_is_not_dropped():
    old = _reload_with(0.50, 0.75)
    try:
        # a capability-ranked survivor with no vector to score
        hits = [{"id": "x", "similarity_score": None}]
        assert [h["id"] for h in rg.apply_relevance_gate(hits)] == ["x"]
    finally:
        _restore(old)


def test_labels_are_measured_bands_only():
    old = _reload_with(0.45, 0.70)
    try:
        assert rg.relevance_label(0.80) == "strong"
        assert rg.relevance_label(0.50) == "relevant"
        assert rg.relevance_label(0.44) is None  # below the gate -> no label
        assert rg.relevance_label(None) is None
    finally:
        _restore(old)


def test_relevance_reason_only_from_query_and_procedure_own_fields():
    reason = rg.relevance_reason(
        "isolate parallel coding agents with git worktrees",
        {"display_name": "Isolate parallel agents",
         "display_description": "Run coding agents concurrently on one repo using git worktrees.",
         "goal": "..."},
    )
    assert reason is not None
    # every word it cites is present in BOTH the query and the procedure text
    q = "isolate parallel coding agents with git worktrees"
    ptext = "isolate parallel agents run coding agents concurrently on one repo using git worktrees"
    cited = reason.split("both mention ", 1)[1].rstrip(".").split(", ")
    assert cited  # it pointed at something
    for w in cited:
        assert w in q and w in ptext


def test_relevance_reason_none_on_pure_semantic_match():
    # query shares no meaningful surface word with the procedure text
    assert rg.relevance_reason(
        "keep context small when many tools exist",
        {"display_name": "Defer schema loading", "display_description": "Lazy-load tool definitions."},
    ) is None


def test_relevance_reason_empty_query():
    assert rg.relevance_reason("", {"display_name": "x"}) is None


def test_gate_never_pads_or_reranks():
    old = _reload_with(0.30, 0.60)
    try:
        hits = [{"id": i, "similarity_score": s} for i, s in
                [("a", 0.9), ("b", 0.2), ("c", 0.8), ("d", 0.25), ("e", 0.7)]]
        kept = rg.apply_relevance_gate(hits)
        assert [h["id"] for h in kept] == ["a", "c", "e"]  # subsequence, same order
    finally:
        _restore(old)


def test_custom_score_key():
    old = _reload_with(0.5, 0.7)
    try:
        hits = [{"id": "a", "native_score": 0.6}, {"id": "b", "native_score": 0.3}]
        kept = rg.apply_relevance_gate(hits, score_key="native_score")
        assert [h["id"] for h in kept] == ["a"]
    finally:
        _restore(old)


def test_version_constant():
    assert isinstance(rg.RELEVANCE_GATE_VERSION, str) and rg.RELEVANCE_GATE_VERSION
