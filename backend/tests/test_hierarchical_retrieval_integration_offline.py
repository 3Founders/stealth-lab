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


def _edge(specific, abstract, specific_name, abstract_name, *, anchor="anchor", side_total=1, relevance=0.0):
    # The hierarchy query returns each edge once per anchor side it touches,
    # already bounded to `max_fanout` rows per side (side_total = visible count).
    side, neighbour = ("parents", abstract) if specific == anchor else ("children", specific)
    return {
        "anchor_id": anchor,
        "side": side,
        "neighbour_id": neighbour,
        "neighbour_name": abstract_name if side == "parents" else specific_name,
        "neighbour_relevance": relevance,
        "side_rank": 1,
        "side_total": side_total,
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
async def test_wide_neighbourhood_is_bounded_not_discarded(monkeypatch):
    # An anchor with 17 children: the query keeps the 8 most relevant (side_total
    # says 17 were visible). Those 8 still go through the contextual judge; the
    # hierarchy is NOT thrown away because the neighbourhood was large.
    anchor = _anchor()
    kept = [
        _edge(f"child-{index}", "anchor", f"Child {index}", "Anchor", side_total=17)
        for index in range(8)
    ]
    pool = FakePool(kept)
    rows = {"anchor": _canonical("anchor", "Anchor")}
    rows.update({f"child-{index}": _canonical(f"child-{index}", f"Child {index}") for index in range(8)})
    captured = {}
    _patched_flow(
        monkeypatch, retrieval.GoalSearchResult([anchor], [anchor], "matches"), captured,
        hydration=HydrationResult(rows=rows),
    )
    judge = _GraphJudge({"Child 3": "matches"})

    response = await retrieval.find_best_way(
        pool,
        "anchor task",
        scope=AccessScope.unrestricted(),
        pools=FakePools(),
        judge=judge,
        record=False,
    )

    assert [goal.id for goal in captured["goals"]] == ["anchor", "child-3"]
    routing = response["retrieval"]["goal_routing"]
    assert routing["mode"] == "hierarchical"
    assert routing["used_flat_fallback"] is False
    assert routing["truncated"] is True
    assert routing["truncation_reasons"] == ["fanout_limit_reached"]
    assert routing["truncated_sides"] == [
        {"goal_id": "anchor", "direction": "children", "kept": 8, "available": 17},
    ]
    assert routing["degraded"] is False
    assert routing["graph_judged"] == 8
    assert response["retrieval"]["degraded"] is False
    # the query itself bounds each side and ranks by relevance to the query
    hierarchy_sql, hierarchy_params = pool.queries[0]
    assert "row_number() OVER (PARTITION BY anchor_id, side" in hierarchy_sql
    assert "side_rank <=" in hierarchy_sql
    assert hierarchy_params[-1] == 8
    assert hierarchy_params[-2] is not None  # the query's terms rank neighbours


@pytest.mark.asyncio
async def test_most_relevant_neighbours_are_judged_first_whatever_their_direction(monkeypatch):
    # 9 neighbours but a judge budget of 8: the child most relevant to the query
    # is judged even though parents are traversed first.
    anchor = _anchor()
    parents = [
        _edge("anchor", f"parent-{index}", "Anchor", f"Parent {index}", relevance=0.0)
        for index in range(8)
    ]
    best_child = _edge("child-best", "anchor", "Child Best", "Anchor", relevance=0.9)
    pool = FakePool([*parents, best_child])
    rows = {"anchor": _canonical("anchor", "Anchor"), "child-best": _canonical("child-best", "Child Best")}
    rows.update({f"parent-{index}": _canonical(f"parent-{index}", f"Parent {index}") for index in range(8)})
    captured = {}
    _patched_flow(
        monkeypatch, retrieval.GoalSearchResult([anchor], [anchor], "matches"), captured,
        hydration=HydrationResult(rows=rows),
    )
    judge = _GraphJudge({"Child Best": "matches"})

    response = await retrieval.find_best_way(
        pool, "anchor task", scope=AccessScope.unrestricted(), pools=FakePools(), judge=judge, record=False,
    )

    assert [goal.id for goal in captured["goals"]] == ["anchor", "child-best"]
    routing = response["retrieval"]["goal_routing"]
    assert routing["graph_judged"] == 8
    assert routing["graph_not_judged_beyond_budget"] == 1


@pytest.mark.asyncio
async def test_anchor_parent_and_child_both_matched_keeps_the_hierarchy(monkeypatch):
    # Search matched both an abstract Goal and one of its specialisations. The
    # one-hop limit is reached (each anchor's neighbour has further edges) --
    # that bounds the walk, it must not switch the hierarchy off.
    abstract = _anchor("abstract", "Abstract")
    specific = _anchor("specific", "Specific")
    edges = [
        _edge("specific", "abstract", "Specific", "Abstract", anchor="specific"),
        _edge("specific", "abstract", "Specific", "Abstract", anchor="abstract"),
        _edge("sibling", "abstract", "Sibling", "Abstract", anchor="abstract"),
        _edge("grandchild", "specific", "Grandchild", "Specific", anchor="specific"),
    ]
    pool = FakePool(edges)
    rows = {goal_id: _canonical(goal_id, goal_id.title()) for goal_id in ("abstract", "specific", "sibling", "grandchild")}
    captured = {}
    _patched_flow(
        monkeypatch, retrieval.GoalSearchResult([abstract, specific], [abstract, specific], "matches"), captured,
        hydration=HydrationResult(rows=rows),
    )
    judge = _GraphJudge({"Sibling": "partial", "Grandchild": "matches"})

    response = await retrieval.find_best_way(
        pool, "task", scope=AccessScope.unrestricted(), pools=FakePools(), judge=judge, record=False,
    )

    assert {goal.id for goal in captured["goals"]} == {"abstract", "specific", "sibling", "grandchild"}
    routing = response["retrieval"]["goal_routing"]
    assert routing["mode"] == "hierarchical"
    assert routing["used_flat_fallback"] is False
    assert "hop_limit_reached" in routing["truncation_reasons"]
    assert response["retrieval"]["degraded"] is False


@pytest.mark.asyncio
async def test_ambiguous_resolution_does_not_expand_the_hierarchy(monkeypatch):
    anchor = _anchor()
    pool = FakePool([_edge("child", "anchor", "Child", "Anchor")])
    captured = {}

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
    assert pool.queries == []
