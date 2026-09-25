"""Live-database tests: a large Goal neighbourhood is bounded, never discarded.

`retrieval_service._route_goal_candidates` used to switch the hierarchy off
entirely (flat fallback) whenever an anchor had more accepted neighbours than
`max_fanout`, or when two anchors were parent and child. The hierarchy query now
keeps at most `max_fanout` neighbours per anchor and direction, ranked by
relevance to the query, and those still go through the contextual Goal judge.
Real SQL, triggers and projections; only the judge's verdicts are scripted.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import persist_goal_relation
from app.services.goals import find_or_create_goal

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

U = AccessScope.unrestricted()
T = TenantScope.unrestricted()


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


class _FixedEmbedder:
    """Stores a caller-chosen 1024-d vector as the Goal's embedding."""

    model = "e2e-fixed-embedder"

    def __init__(self, vector):
        self.vector = vector

    async def embed_one_with_metadata(self, text, input_type="document"):
        return self.vector, SimpleNamespace(
            model_id=self.model, provider="e2e", text_sha256=hashlib.sha256(text.encode()).hexdigest())


def _axis(position: int) -> list[float]:
    vector = [0.0] * 1024
    vector[position] = 1.0
    return vector


async def _goal(pool, name: str, vector=None) -> str:
    created = await find_or_create_goal(
        pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
        judge_mode="none", status="active", embedder=_FixedEmbedder(vector) if vector else None,
    )
    await sp.drain_outbox(pool)  # an accepted edge needs both endpoints projected
    return created["id"]


async def _edge(pool, specific: str, abstract: str) -> None:
    await persist_goal_relation(
        pool, specific, abstract, status="accepted", provenance="e2e", access_scope=U,
        tenant_scope=T, decided_by="e2e-reviewer", decision_metadata={"reason": "e2e"},
    )


class _Verdict:
    ok = True
    provider = "scripted"

    def __init__(self, relation: str):
        self.value = {"relation": relation, "confidence": 0.9}


class _ScriptedJudge:
    """Admits exactly the Goals whose text contains one of `admit`."""

    def __init__(self, *admit: str):
        self.admit = admit
        self.judged: list[str] = []

    async def judge_identity(self, kind, context, text):
        self.judged.append(text)
        return _Verdict("matches" if any(word in text for word in self.admit) else "unrelated")


def _anchor(goal_id: str, name: str) -> rs.Hit:
    return rs.Hit(goal_id, name, name, "K000", rrf=0.9, relation="matches", confidence=0.95, judged=True)


async def _route(pool, anchors: list[rs.Hit], query: str, judge: _ScriptedJudge, query_vector=None):
    meta = rs.RetrievalMeta()
    ctx = await rs.build_query_context(query, [], embedder=None, cfg=rs.RetrievalConfig())
    if query_vector is not None:
        ctx.query_embedding, ctx.embedding_model = query_vector, _FixedEmbedder.model
    routed = await rs._route_goal_candidates(
        pool, rs.GoalSearchResult(anchors, list(anchors), "matches"), scope=U, cfg=rs.RetrievalConfig(),
        meta=meta, tenant_scope=T, ctx=ctx, judge=judge,
    )
    return routed, meta


@pytest.mark.asyncio
async def test_wide_abstract_goal_keeps_the_most_relevant_children(pool):
    run = uuid.uuid4().hex[:8]
    abstract = await _goal(pool, f"e2e {run} document export")
    # 11 children that do not mention the query, plus the one that does. Its name
    # sorts LAST alphabetically, so only relevance ranking keeps it within the 8.
    for index in range(11):
        await _edge(pool, await _goal(pool, f"e2e {run} child {index:02d} spreadsheet import"), abstract)
    wanted = await _goal(pool, f"e2e {run} zulu export docx from react app")
    await _edge(pool, wanted, abstract)
    await sp.drain_outbox(pool)

    judge = _ScriptedJudge("docx")
    routed, meta = await _route(pool, [_anchor(abstract, "document export")], "export docx from react", judge)

    assert [goal.id for goal in routed] == [abstract, wanted]
    routing = meta.goal_routing
    assert routing["mode"] == "hierarchical" and routing["used_flat_fallback"] is False
    assert routing["truncated"] is True and routing["degraded"] is False
    assert routing["truncated_sides"] == [
        {"goal_id": abstract, "direction": "children", "kept": 8, "available": 12},
    ]
    assert meta.counts["goal_hierarchy_graph_candidates"] == 8
    assert judge.judged[0].startswith(f"e2e {run} zulu")  # most relevant neighbour judged first
    assert not meta.degraded_reasons


@pytest.mark.asyncio
async def test_parent_and_child_both_matched_still_expand(pool):
    run = uuid.uuid4().hex[:8]
    root = await _goal(pool, f"e2e {run} build pipeline")
    mid = await _goal(pool, f"e2e {run} build pipeline for python")
    leaf = await _goal(pool, f"e2e {run} build pipeline for python wheels")
    sibling = await _goal(pool, f"e2e {run} build pipeline for rust")
    await _edge(pool, mid, root)
    await _edge(pool, leaf, mid)
    await _edge(pool, sibling, root)
    await sp.drain_outbox(pool)

    judge = _ScriptedJudge("wheels", "rust")
    routed, meta = await _route(
        pool, [_anchor(root, "build pipeline"), _anchor(mid, "build pipeline for python")],
        "build python wheels", judge,
    )

    assert {goal.id for goal in routed} == {root, mid, leaf, sibling}
    routing = meta.goal_routing
    assert routing["mode"] == "hierarchical" and routing["used_flat_fallback"] is False
    assert not meta.degraded_reasons


@pytest.mark.asyncio
async def test_neighbours_are_ranked_by_meaning_not_shared_words(pool):
    # The right child shares NO word with the query; only its embedding is close.
    run = uuid.uuid4().hex[:8]
    abstract = await _goal(pool, f"e2e {run} produce office files", _axis(0))
    for index in range(11):
        # these children share the word "export" with the query but mean something else
        await _edge(pool, await _goal(pool, f"e2e {run} export child {index:02d} of logs", _axis(10 + index)), abstract)
    wanted = await _goal(pool, f"e2e {run} write a word document", _axis(1))
    await _edge(pool, wanted, abstract)

    judge = _ScriptedJudge("word document")
    routed, meta = await _route(
        pool, [_anchor(abstract, "produce office files")], "export docx", judge, query_vector=_axis(1),
    )

    assert wanted in [goal.id for goal in routed]
    assert judge.judged[0].startswith(f"e2e {run} write a word document")


@pytest.mark.asyncio
async def test_two_hops_reach_a_grandchild(pool):
    run = uuid.uuid4().hex[:8]
    root = await _goal(pool, f"e2e {run} ship software")
    child = await _goal(pool, f"e2e {run} ship a python package")
    grandchild = await _goal(pool, f"e2e {run} ship a python wheel to pypi")
    await _edge(pool, child, root)
    await _edge(pool, grandchild, child)

    judge = _ScriptedJudge("wheel")
    routed, meta = await _route(pool, [_anchor(root, "ship software")], "publish a wheel", judge)

    assert [goal.id for goal in routed] == [root, grandchild]
    path = next(p for p in meta.goal_routing["paths"] if p["abstraction_path"][-1] == grandchild)
    assert path["abstraction_path"] == [root, child, grandchild]
