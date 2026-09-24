from __future__ import annotations

import pytest

import app.services.retrieval_service as retrieval
from app.services.access import AccessScope, TenantScope
from app.services.shards import HydrationResult


class FakePool:
    def __init__(self, rows=(), error: Exception | None = None):
        self.rows = list(rows)
        self.error = error
        self.queries = []

    async def fetch(self, sql, *params):
        self.queries.append((" ".join(sql.split()), params))
        if self.error is not None:
            raise self.error
        return self.rows


class FakePools:
    pass


def _canonical(goal_id, name, version=1):
    return {
        "id": goal_id,
        "canonical_name": name,
        "description": f"{name} description",
        "status": "active",
        "scope_type": "global",
        "scope_entity_id": None,
        "visibility": "public",
        "owner_id": None,
        "version": version,
        "home_shard_id": "K000",
        "t_invalid": None,
    }


def _edge(specific, abstract, specific_name, abstract_name):
    return {
        "specific_goal_id": specific,
        "abstract_goal_id": abstract,
        "relation_type": "SPECIALIZES",
        "status": "accepted",
        "confidence": 0.9,
        "provenance": "integration_test",
        "edge_id": "edge-anchor-parent",
        "relation_id": "relation-anchor-parent",
        "relation_version_id": "relation-version-anchor-parent",
        "decision_id": "decision-secret",
        "decision_metadata": {"secret": "audit-json"},
        "decided_by": "reviewer-secret",
        "decided_at": "2026-09-24T00:00:00Z",
        "specific_projection_id": specific,
        "specific_canonical_name": specific_name,
        "specific_description": f"{specific_name} description",
        "specific_projected_status": "active",
        "specific_projected_version": 1,
        "specific_projected_scope_type": "global",
        "specific_projected_scope_entity_id": None,
        "specific_projected_visibility": "public",
        "specific_projected_owner_id": None,
        "specific_home_shard_id": "K000",
        "abstract_projection_id": abstract,
        "abstract_canonical_name": abstract_name,
        "abstract_description": f"{abstract_name} description",
        "abstract_projected_status": "active",
        "abstract_projected_version": 1,
        "abstract_projected_scope_type": "global",
        "abstract_projected_scope_entity_id": None,
        "abstract_projected_visibility": "public",
        "abstract_projected_owner_id": None,
        "abstract_home_shard_id": "K000",
    }


def _anchor(goal_id="anchor", name="Anchor"):
    return retrieval.Hit(goal_id, name, name, "K000", rrf=0.9, relation="matches", confidence=0.95, judged=True)


def _patched_flow(monkeypatch, goal_result, captured, hydration=None):
    async def fake_search_goals(*args, **kwargs):
        return goal_result

    async def fake_retrieve_procedures(*args, **kwargs):
        captured["goals"] = list(args[2])
        return retrieval.ProcedureSearchResult([], None, [], [], "no procedures")

    async def fake_hydrate(*args, **kwargs):
        return hydration or HydrationResult(rows={
            "anchor": _canonical("anchor", "Anchor"),
            "parent": _canonical("parent", "Parent"),
            "child": _canonical("child", "Child"),
        })

    monkeypatch.setattr(retrieval, "search_goals", fake_search_goals)
    monkeypatch.setattr(retrieval, "retrieve_procedures", fake_retrieve_procedures)
    monkeypatch.setattr(retrieval, "hydrate_rows", fake_hydrate)


class _Verdict:
    def __init__(self, relation):
        self.ok = True
        self.value = {"relation": relation, "confidence": 0.9}
        self.provider = "fake"


class _GraphJudge:
    def __init__(self, relations):
        self.relations = relations
        self.kinds = set()

    async def judge_identity(self, kind, a, b):
        self.kinds.add(kind)
        name = b.split(":", 1)[0]
        return _Verdict(self.relations.get(name, "unrelated"))


@pytest.mark.asyncio
async def test_accepted_edges_expand_after_anchors_without_score_or_applicability(monkeypatch):
    anchor = _anchor()
    flat_candidate = retrieval.Hit("flat", "Flat", "Flat", "K000", rrf=0.8)
    result = retrieval.GoalSearchResult([anchor], [anchor, flat_candidate], "matches")
    pool = FakePool([
        _edge("anchor", "parent", "Anchor", "Parent"),
        _edge("child", "anchor", "Child", "Anchor"),
    ])
    captured = {}
    _patched_flow(monkeypatch, result, captured)

    judge = _GraphJudge({"Parent": "partial", "Child": "unrelated"})
    response = await retrieval.find_best_way(
        pool,
        "anchor task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        judge=judge,
        record=False,
        tenant_scope=TenantScope.commons(),
    )

    # Graph neighbours are judged in context like any other Goal candidate;
    # only the one the judge admits reaches Procedure retrieval.
    assert [goal.id for goal in captured["goals"]] == ["anchor", "parent"]
    assert judge.kinds == {"task_goal"}
    parent = captured["goals"][1]
    assert parent.rrf == 0.0 and parent.judged is True and parent.relation == "partial"
    routing = response["retrieval"]["goal_routing"]
    assert routing["graph_judged"] == 2 and routing["graph_admitted"] == 1
    assert response["goal_resolution"]["status"] == "matches"
    assert [candidate["id"] for candidate in response["goal_resolution"]["candidates"]] == [
        "anchor", "flat", "parent", "child",
    ]
    graph = next(candidate for candidate in response["goal_resolution"]["candidates"] if candidate["id"] == "parent")
    assert graph["hierarchy"]["origin"] == "graph"
    assert graph["hierarchy"]["abstraction_path"] == ["anchor", "parent"]
    relation = graph["hierarchy"]["relation_provenance"][0]
    assert relation["id"] == "edge-anchor-parent"
    assert relation["name"] == "SPECIALIZES"
    assert relation["status"] == "accepted"
    assert relation["confidence"] == 0.9
    assert relation["path"] == ["anchor", "parent"]
    assert "decision_id" not in relation
    assert "decision_metadata" not in relation
    assert "decided_by" not in relation
    assert "decided_at" not in relation
    assert "provenance" not in relation
    assert "decision-secret" not in repr(response)
    assert "audit-json" not in repr(response)
    assert response["retrieval"]["goal_routing"]["mode"] == "hierarchical"
    assert response["retrieval"]["goal_routing"]["paths"]
    hierarchy_sql, hierarchy_params = pool.queries[0]
    assert "r.tenant_id =" in hierarchy_sql
    assert "s.tenant_id =" not in hierarchy_sql
    assert "n.tenant_id =" not in hierarchy_sql
    assert "s.visibility" in hierarchy_sql
    assert "n.visibility" in hierarchy_sql
    assert hierarchy_params[1] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_no_edge_returns_flat_anchor_and_explicit_diagnostics(monkeypatch):
    anchor = _anchor()
    result = retrieval.GoalSearchResult([anchor], [anchor], "matches")
    pool = FakePool([])
    captured = {}
    _patched_flow(monkeypatch, result, captured)

    response = await retrieval.find_best_way(
        pool,
        "anchor task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        record=False,
    )

    assert [goal.id for goal in captured["goals"]] == ["anchor"]
    routing = response["retrieval"]["goal_routing"]
    assert routing["mode"] == "flat"
    assert routing["used_flat_fallback"] is True
    assert routing["fallback_reasons"] == ["no_edges"]
    assert "r.status = 'accepted'" in pool.queries[0][0]
    assert "r.tenant_id" not in pool.queries[0][0]



@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query_error", "unavailable", "stale"])
async def test_hierarchy_failure_returns_flat_results_with_diagnostics(monkeypatch, failure):
    anchor = _anchor()
    result = retrieval.GoalSearchResult([anchor], [anchor], "matches")
    edge = _edge("anchor", "child", "Anchor", "Child")
    if failure == "query_error":
        pool = FakePool(error=RuntimeError("offline"))
        hydration = None
    else:
        pool = FakePool([edge])
        rows = {
            "anchor": _canonical("anchor", "Anchor"),
            "child": _canonical("child", "Child", version=2 if failure == "stale" else 1),
        }
        hydration = HydrationResult(
            rows=rows,
            unavailable_shards={"K001": "offline"} if failure == "unavailable" else {},
        )
    captured = {}
    _patched_flow(monkeypatch, result, captured, hydration=hydration)

    response = await retrieval.find_best_way(
        pool,
        "anchor task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        record=False,
    )

    assert [goal.id for goal in captured["goals"]] == ["anchor"]
    routing = response["retrieval"]["goal_routing"]
    assert routing["mode"] == "flat"
    assert routing["used_flat_fallback"] is True
    assert routing["fallback_reasons"]
    assert response["retrieval"]["degraded"] is True


@pytest.mark.asyncio
async def test_truncation_returns_flat_results_and_ambiguous_resolution_is_unchanged(monkeypatch):
    anchor = _anchor()
    edges = [
        _edge("anchor", f"child-{index}", "Anchor", f"Child {index}")
        for index in range(17)
    ]
    pool = FakePool(edges)
    captured = {}
    _patched_flow(monkeypatch, retrieval.GoalSearchResult([anchor], [anchor], "matches"), captured)

    truncated = await retrieval.find_best_way(
        pool,
        "anchor task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        record=False,
    )
    assert [goal.id for goal in captured["goals"]] == ["anchor"]
    assert truncated["retrieval"]["goal_routing"]["fallback_reasons"] == ["graph_edge_limit_reached"]
    assert truncated["retrieval"]["goal_routing"]["truncated"] is True

    async def unexpected_query(*args, **kwargs):
        raise AssertionError("ambiguous resolution must not query hierarchy")

    monkeypatch.setattr(retrieval, "pools_for", unexpected_query)
    captured["goals"] = []
    _patched_flow(
        monkeypatch,
        retrieval.GoalSearchResult([anchor], [anchor], "partial"),
        captured,
    )
    unchanged = await retrieval.find_best_way(
        pool,
        "ambiguous task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        record=False,
    )
    assert [goal.id for goal in captured["goals"]] == ["anchor"]
    assert unchanged["goal_resolution"]["status"] == "partial"
    assert unchanged["retrieval"]["goal_routing"]["fallback_reasons"] == ["semantic_resolution_not_matches"]
