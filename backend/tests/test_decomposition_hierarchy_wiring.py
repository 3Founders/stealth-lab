"""
Tests for DecompositionService._try_hierarchical_match (Part B wiring
into decompose()).

The thing that actually matters here: this must ONLY short-circuit on a
confident full match, and must be silently absent (fall through to the
existing flat find_reusable_nodes path) on anything else -- low
confidence, the tree's own fallback signal, or an outright exception.
Existing tests/test_decomposition.py never constructs a DecompositionService
with a retriever at all, so none of that suite actually exercises this
code path -- these tests close that gap.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.decomposition import DecompositionService
from app.services.hierarchy import SearchResult


def _make_service_with_retriever():
    retriever = MagicMock()
    retriever._pool = MagicMock()
    retriever._scope = MagicMock()
    retriever._embedder = MagicMock()
    service = DecompositionService(generator=MagicMock(), retriever=retriever)
    return service


def test_confident_full_match_short_circuits():
    service = _make_service_with_retriever()
    confident = SearchResult(
        leaf_id="node-1", leaf_name="Extract invoice fields",
        similarity=0.95, used_flat_fallback=False, comparisons=6,
    )
    with patch(
        "app.services.decomposition.hierarchical_search",
        new=AsyncMock(return_value=confident),
    ):
        result = asyncio.run(service._try_hierarchical_match("extract fields from invoice"))

    assert result is not None
    assert result.id == "node-1"
    assert result.method == "vector"
    assert result.is_full_match is True


def test_low_confidence_falls_through():
    service = _make_service_with_retriever()
    weak = SearchResult(
        leaf_id="node-2", leaf_name="Something vaguely related",
        similarity=0.55, used_flat_fallback=False, comparisons=6,
    )
    with patch(
        "app.services.decomposition.hierarchical_search",
        new=AsyncMock(return_value=weak),
    ):
        result = asyncio.run(service._try_hierarchical_match("some problem"))

    assert result is None  # caller must fall through to find_reusable_nodes


def test_flat_fallback_signal_is_respected():
    service = _make_service_with_retriever()
    aborted = SearchResult(
        leaf_id=None, leaf_name=None, similarity=None,
        used_flat_fallback=True, comparisons=2,
    )
    with patch(
        "app.services.decomposition.hierarchical_search",
        new=AsyncMock(return_value=aborted),
    ):
        result = asyncio.run(service._try_hierarchical_match("some problem"))

    assert result is None


def test_exception_in_tree_search_falls_through_not_raises():
    service = _make_service_with_retriever()
    with patch(
        "app.services.decomposition.hierarchical_search",
        new=AsyncMock(side_effect=RuntimeError("db exploded")),
    ):
        result = asyncio.run(service._try_hierarchical_match("some problem"))

    assert result is None  # must not propagate -- this is a hot path


def test_no_retriever_returns_none_without_calling_search():
    service = DecompositionService(generator=MagicMock(), retriever=None)
    with patch(
        "app.services.decomposition.hierarchical_search",
        new=AsyncMock(),
    ) as mocked:
        result = asyncio.run(service._try_hierarchical_match("some problem"))
    assert result is None
    mocked.assert_not_called()


def test_decompose_threads_query_postconditions_to_hierarchical_match():
    """
    Confirms decompose()'s new query_postconditions parameter actually
    reaches hierarchical_search, not just that it's accepted and
    silently dropped somewhere along the way.
    """
    service = _make_service_with_retriever()
    captured = {}

    async def capture_search(pool, table, problem, **kwargs):
        captured[table] = kwargs.get("query_postconditions")
        return SearchResult(None, None, None, used_flat_fallback=True, comparisons=0)

    with patch("app.services.decomposition.hierarchical_search", new=capture_search):
        asyncio.run(service._try_hierarchical_match(
            "some problem", query_postconditions=["schema_conformance"],
        ))

    assert captured["task_nodes"] == ["schema_conformance"]
    assert captured["knowledge_nodes"] == ["schema_conformance"]
    service = _make_service_with_retriever()
    task_result = SearchResult(
        leaf_id="task-1", leaf_name="Task match", similarity=0.91,
        used_flat_fallback=False, comparisons=6,
    )
    knowledge_result = SearchResult(
        leaf_id="know-1", leaf_name="Knowledge match", similarity=0.97,
        used_flat_fallback=False, comparisons=6,
    )

    async def fake_search(pool, table, *args, **kwargs):
        return task_result if table == "task_nodes" else knowledge_result

    with patch("app.services.decomposition.hierarchical_search", new=AsyncMock(side_effect=fake_search)):
        result = asyncio.run(service._try_hierarchical_match("some problem"))

    assert result is not None
    assert result.id == "know-1"  # the higher-similarity one, regardless of table order
