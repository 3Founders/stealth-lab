"""
Offline (no real DB, no real network) thin-wrapper tests for the Goal
REST API (app/api/goals.py) -- same direct-call convention
test_procedure_fast_create_offline.py already established (Depends()
params overridden with explicit keyword args, no ASGITransport needed
since this layer's own logic, not FastAPI's dependency wiring, is what's
under test). Does NOT re-test app/services/goals.py's own dedup/search
logic (covered by tests/test_goals_offline.py).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.api.goals as goals_api
from app.api.deps import AuthenticatedPrincipal
from app.services.access import AccessScope, TenantScope
from app.services.v0_gate import V0Violation

PRINCIPAL = AuthenticatedPrincipal(user_id="user-uuid-1", subject="supabase-uid-1", email="c@x.dev")


def _run(coro):
    return asyncio.run(coro)


# --- search_goals_route -----------------------------------------------

def test_search_goals_route_lexical_only_when_semantic_explicitly_false(monkeypatch):
    """`semantic`'s real HTTP default is proven separately (real
    FastAPI/Starlette parameter resolution) in
    test_search_goals_route_default_over_real_http_is_lexical_only below
    -- a direct Python call like this one bypasses Query() default
    resolution entirely (its unresolved default is the Query(...) sentinel
    object itself, which is truthy), so `semantic` must be passed
    explicitly here to mean anything."""
    captured = {}

    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        captured["query_embedding"] = query_embedding
        return [{"id": "g1", "canonical_name": "find references"}]

    monkeypatch.setattr("app.api.goals.search_goals", fake_search)
    result = _run(goals_api.search_goals_route(
        q="find refs", scope_type=None, semantic=False,
        pool=object(), scope=AccessScope.unrestricted(),
    ))
    assert captured["query_embedding"] is None
    assert result == [{"id": "g1", "canonical_name": "find references"}]


def test_search_goals_route_default_over_real_http_is_lexical_only(monkeypatch):
    """The real proof of the default: dispatched through actual FastAPI
    routing (httpx.ASGITransport against app.main.app), not a direct
    Python call -- this is the layer that actually resolves Query()
    defaults, which the test above cannot exercise."""
    import httpx

    import app.main as main_module
    from app.api.goals import get_pool

    captured = {}

    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        captured["query_embedding"] = query_embedding
        return []

    monkeypatch.setattr("app.api.goals.search_goals", fake_search)
    main_module.app.dependency_overrides[get_pool] = lambda: object()

    async def _run_request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://goals") as client:
            resp = await client.get("/v1/goals/search", params={"q": "find refs"})
            assert resp.status_code == 200

    try:
        _run(_run_request())
    finally:
        main_module.app.dependency_overrides.pop(get_pool, None)
    assert captured["query_embedding"] is None


def test_search_goals_route_semantic_true_computes_a_real_embedding(monkeypatch):
    captured = {}

    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        captured["query_embedding"] = query_embedding
        return []

    class _FakeEmbedder:
        async def embed_one_with_metadata(self, text, input_type="document"):
            return [0.1, 0.2], object()

    monkeypatch.setattr("app.api.goals.search_goals", fake_search)
    monkeypatch.setattr("app.services.embeddings.Embedder", _FakeEmbedder)
    _run(goals_api.search_goals_route(
        q="find refs", semantic=True, pool=object(), scope=AccessScope.unrestricted(),
    ))
    assert captured["query_embedding"] == [0.1, 0.2]


def test_search_goals_route_narrows_by_scope_type(monkeypatch):
    async def fake_search(pool, *, query_text, query_embedding, scope, status, limit):
        return [
            {"id": "g1", "scope_type": "global", "scope_entity_id": None},
            {"id": "g2", "scope_type": "project", "scope_entity_id": "repo-a"},
        ]

    monkeypatch.setattr("app.api.goals.search_goals", fake_search)
    result = _run(goals_api.search_goals_route(
        q="x", scope_type="project", scope_entity_id="repo-a", semantic=False,
        pool=object(), scope=AccessScope.unrestricted(),
    ))
    assert [r["id"] for r in result] == ["g2"]


# --- inspect_goal_route -------------------------------------------------

def test_inspect_goal_route_404s_on_missing_or_invisible(monkeypatch):
    async def fake_get(pool, goal_id, *, scope):
        return None

    monkeypatch.setattr("app.api.goals.get_goal", fake_get)
    with pytest.raises(HTTPException) as excinfo:
        _run(goals_api.inspect_goal_route(
            goal_id="missing",
            pool=object(),
            scope=AccessScope.unrestricted(),
            tenant_scope=TenantScope.commons(),
        ))
    assert excinfo.value.status_code == 404


def test_inspect_goal_route_returns_the_full_record(monkeypatch):
    scope = AccessScope.unrestricted()
    captured = {}

    async def fake_get(pool, goal_id, *, scope):
        captured["get_access_scope"] = scope
        return {"id": goal_id, "canonical_name": "x"}

    async def fake_enrich(pool, goal, *, access_scope, tenant_scope):
        captured.update(access_scope=access_scope, tenant_scope=tenant_scope)
        return {
            **goal,
            "abstraction_level": 0,
            "specializes": [],
            "abstracts": [],
            "benchmarks": [],
        }

    monkeypatch.setattr("app.api.goals.get_goal", fake_get)
    monkeypatch.setattr("app.api.goals.enrich_goal", fake_enrich)
    tenant_scope = TenantScope.commons()
    result = _run(goals_api.inspect_goal_route(
        goal_id="g1", pool=object(), scope=scope, tenant_scope=tenant_scope,
    ))
    assert result["id"] == "g1"
    assert result["specializes"] == [] and result["abstracts"] == []
    assert result["benchmarks"] == [] and result["abstraction_level"] == 0
    assert captured["access_scope"] is scope
    assert "get_tenant_scope" not in captured
    assert captured["tenant_scope"] is tenant_scope


def test_inspect_goal_route_keeps_canonical_goal_when_hierarchy_projection_lags(monkeypatch):
    tenant_scope = TenantScope.commons()
    scope = AccessScope.anonymous()
    canonical = {
        "id": "g1",
        "canonical_name": "Canonical goal",
        "status": "active",
        "procedures": [],
    }

    async def fake_get(pool, goal_id, *, scope):
        return canonical

    async def fake_enrich(pool, goal, *, access_scope, tenant_scope):
        return None

    async def fake_benchmarks(pool, goal_id, *, scope):
        return [{"id": "b1", "goal_id": goal_id}]

    monkeypatch.setattr("app.api.goals.get_goal", fake_get)
    monkeypatch.setattr("app.api.goals.enrich_goal", fake_enrich)
    monkeypatch.setattr("app.api.goals.pm.list_goal_benchmarks", fake_benchmarks)
    result = _run(goals_api.inspect_goal_route(
        goal_id="g1", pool=object(), scope=scope, tenant_scope=tenant_scope,
    ))

    assert result["id"] == "g1"
    assert result["canonical_name"] == "Canonical goal"
    # Unknown, not "a root with no relations": the page must not claim a level.
    assert result["hierarchy_available"] is False
    assert result["specializes"] is None
    assert result["abstracts"] is None
    assert result["abstraction_level"] is None
    assert result["coverage"] is None
    # Benchmarks don't depend on the hierarchy and still come from benchmarks.goal_id.
    assert result["benchmarks"] == [{"id": "b1", "goal_id": "g1"}]


# --- create_goal_route ---------------------------------------------------

def test_create_goal_route_owner_is_the_authenticated_principal(monkeypatch):
    captured = {}

    async def fake_create(pool, **kw):
        captured.update(kw)
        return {"outcome": "created", "goal": {"id": "g1"}}

    monkeypatch.setattr("app.api.goals.create_goal_from_user", fake_create)
    body = goals_api.GoalCreateBody(canonical_name="reconcile schema drift", use_embeddings=False)
    result = _run(goals_api.create_goal_route(body, pool=object(), principal=PRINCIPAL))
    assert captured["owner_id"] == PRINCIPAL.subject
    assert captured["embedder"] is None
    assert result == {"outcome": "created", "goal": {"id": "g1"}}


def test_create_goal_route_v0_violation_becomes_422(monkeypatch):
    async def fake_create(pool, **kw):
        raise V0Violation("V0: bad scope")

    monkeypatch.setattr("app.api.goals.create_goal_from_user", fake_create)
    body = goals_api.GoalCreateBody(canonical_name="x", use_embeddings=False)
    with pytest.raises(HTTPException) as excinfo:
        _run(goals_api.create_goal_route(body, pool=object(), principal=PRINCIPAL))
    assert excinfo.value.status_code == 422


def test_create_goal_route_provider_policy_denial_retries_without_an_embedder(monkeypatch):
    from app.services.provider_policy import PolicyDecision, ProviderPolicyDenied

    calls = []

    async def fake_create(pool, *, embedder, **kw):
        calls.append(embedder)
        if embedder is not None:
            raise ProviderPolicyDenied(PolicyDecision(
                allowed=False, reason="external provider not permitted",
                provider="fake", model="fake-model", data_classification="user_private",
            ))
        return {"outcome": "created", "goal": {"id": "g1"}}

    class _FakeEmbedder:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr("app.api.goals.create_goal_from_user", fake_create)
    monkeypatch.setattr("app.services.embeddings.Embedder", _FakeEmbedder)
    body = goals_api.GoalCreateBody(canonical_name="x", use_embeddings=True)
    result = _run(goals_api.create_goal_route(body, pool=object(), principal=PRINCIPAL))
    assert len(calls) == 2
    assert calls[0] is not None
    assert calls[1] is None
    assert result == {"outcome": "created", "goal": {"id": "g1"}}


def test_create_goal_route_returns_near_matches_without_writing(monkeypatch):
    async def fake_create(pool, **kw):
        return {"outcome": "near_matches", "candidates": [{"id": "existing-1"}]}

    monkeypatch.setattr("app.api.goals.create_goal_from_user", fake_create)
    body = goals_api.GoalCreateBody(canonical_name="x", use_embeddings=False)
    result = _run(goals_api.create_goal_route(body, pool=object(), principal=PRINCIPAL))
    assert result["outcome"] == "near_matches"
