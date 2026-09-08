"""
DB-free coverage for app/services/domain_search.py + app/api/search.py.

This module is pure composition over applicability.py/retrieval.py --
those modules' own cascade/RRF correctness is already proven by
test_applicability_*_offline.py and test_retrieval_rrf_properties.py.
What's genuinely under test HERE is domain_search's OWN logic: object-
type validation, the V0 scope_type/repository_id/project_id post-filter,
result grouping (never cross-type ranking), honest-empty-result behavior,
and the REST shapes in app/api/search.py. Composed dependencies
(find_applicable_procedures, check_procedure_reuse, HybridRetriever) are
monkeypatched with canned returns -- same idiom test_chat.py already uses
for ChatService's own graph dependency.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.search as search_api
import app.services.domain_search as ds
from app.services.access import AccessScope
from app.services.applicability import ProcedureNotFound, ProcedureVerdict


def _run(coro):
    return asyncio.run(coro)


class FakeEmbedder:
    """Deterministic, network-free stand-in -- same shape
    test_applicability_e2e.py's own FakeEmbedder uses."""

    async def embed_one(self, text, input_type="document"):
        return [0.1] * 8

    def embedding_model_id(self):
        return "fake:test-embed"

    def _configured_provider(self):
        return "fake"


def _procedure_row(pid="00000000-0000-4000-8000-000000000001", **overrides):
    row = {
        "id": pid,
        "procedure_id": pid,
        "name": "deploy the thing",
        "goal": "deploy safely",
        "verification_state": "verified",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "approved",
        "scope_type": "repository",
        "scope_entity_id": "repo-1",
        "_similarity_score": 0.9,
        "version": 1,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# _resolve_object_types / object-type validation -- pure, no I/O.
# ---------------------------------------------------------------------------

def test_default_object_types_is_all_three_in_defined_order():
    assert ds._resolve_object_types(None) == ["procedure", "task", "claim"]


def test_object_types_deduped_preserving_first_occurrence_order():
    assert ds._resolve_object_types(["claim", "procedure", "claim"]) == ["claim", "procedure"]


def test_solution_object_type_raises_with_a_precise_reason():
    with pytest.raises(ValueError, match="not a distinct searchable entity"):
        ds._resolve_object_types(["solution"])


def test_problem_object_type_raises_with_a_precise_reason():
    with pytest.raises(ValueError, match="does not exist in the schema yet"):
        ds._resolve_object_types(["problem"])


def test_unknown_object_type_raises_a_generic_valueerror():
    with pytest.raises(ValueError, match="unknown object_type"):
        ds._resolve_object_types(["widget"])


# ---------------------------------------------------------------------------
# _scope_filter_matches -- pure, no I/O.
# ---------------------------------------------------------------------------

def test_scope_filter_no_constraints_always_matches():
    assert ds._scope_filter_matches(None, None, scope_type=None, repository_id=None, project_id=None)


def test_scope_filter_scope_type_mismatch_excludes():
    assert not ds._scope_filter_matches(
        "project", "p1", scope_type="repository", repository_id=None, project_id=None,
    )


def test_scope_filter_repository_id_requires_matching_type_and_id():
    assert ds._scope_filter_matches(
        "repository", "repo-1", scope_type=None, repository_id="repo-1", project_id=None,
    )
    assert not ds._scope_filter_matches(
        "repository", "repo-2", scope_type=None, repository_id="repo-1", project_id=None,
    )
    assert not ds._scope_filter_matches(
        "project", "repo-1", scope_type=None, repository_id="repo-1", project_id=None,
    )


def test_scope_filter_project_id_requires_matching_type_and_id():
    assert ds._scope_filter_matches(
        "project", "proj-1", scope_type=None, repository_id=None, project_id="proj-1",
    )
    assert not ds._scope_filter_matches(
        "project", "proj-2", scope_type=None, repository_id=None, project_id="proj-1",
    )


def test_scope_filter_combines_all_supplied_conditions_with_and():
    row = ("repository", "repo-1")
    assert ds._scope_filter_matches(
        *row, scope_type="repository", repository_id="repo-1", project_id=None,
    )
    assert not ds._scope_filter_matches(
        *row, scope_type="project", repository_id="repo-1", project_id=None,
    )


# ---------------------------------------------------------------------------
# search_global -- procedure leg composes find_applicable_procedures
# verbatim, then applies the V0 scope post-filter + limit.
# ---------------------------------------------------------------------------

def test_search_global_procedure_leg_shapes_and_postfilters(monkeypatch):
    survivors = [
        _procedure_row("00000000-0000-4000-8000-000000000001", scope_type="repository", scope_entity_id="repo-1"),
        _procedure_row("00000000-0000-4000-8000-000000000002", scope_type="repository", scope_entity_id="repo-2"),
    ]

    async def fake_find_applicable_procedures(pool, **kwargs):
        assert kwargs["require_verified"] is False  # search's own browse-mode default
        return survivors

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)

    result = _run(ds.search_global(
        pool=object(), query="deploy", object_types=["procedure"],
        repository_id="repo-1", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))

    assert result["object_types"] == ["procedure"]
    assert result["counts"] == {"procedure": 1}
    assert len(result["results"]["procedure"]) == 1
    hit = result["results"]["procedure"][0]
    assert hit["scope_entity_id"] == "repo-1"
    assert hit["id"] == "00000000-0000-4000-8000-000000000001"
    assert hit["similarity_score"] == 0.9


def test_search_global_procedure_leg_honest_empty_when_no_survivors(monkeypatch):
    async def fake_find_applicable_procedures(pool, **kwargs):
        return []

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)

    result = _run(ds.search_global(
        pool=object(), query="deploy", object_types=["procedure"],
        scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))
    assert result["results"]["procedure"] == []
    assert result["counts"]["procedure"] == 0


# ---------------------------------------------------------------------------
# search_global -- claim leg reuses HybridRetriever, post-filters to
# node_type='claim' via a batched hydrate.
# ---------------------------------------------------------------------------

class _FakePoolForClaimFetch:
    """Only ever reached by _fetch_claim_fields' batched hydrate query --
    proves the post-filter keeps only ids this fake reports as real,
    live claims."""

    def __init__(self, claim_rows):
        self._claim_rows = claim_rows
        self.fetch_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        return self._claim_rows


class _FakeHybridRetriever:
    """Stands in for HybridRetriever at the level domain_search actually
    calls it: `_vector_search`/`_lexical_search` directly, never
    `.retrieve()` -- see domain_search's own "CLAIM/TASK RETRIEVAL
    MECHANISM" docstring for why (retrieve()'s expansion stage has a
    confirmed live bug this module deliberately routes around)."""

    def __init__(self, vector_hits=(), lexical_hits=()):
        self._vector_hits = list(vector_hits)
        self._lexical_hits = list(lexical_hits)

    def __call__(self, *a, **kw):
        return self

    async def _vector_search(self, query_vec, limit):
        return self._vector_hits

    async def _lexical_search(self, query, limit):
        return self._lexical_hits


def test_search_global_claim_leg_postfilters_non_claim_hits(monkeypatch):
    claim_id = uuid4()
    non_claim_id = uuid4()  # e.g. a 'policy' knowledge_node the RRF legs also matched

    fake_retriever = _FakeHybridRetriever(
        lexical_hits=[(claim_id, "knowledge_nodes", 0), (non_claim_id, "knowledge_nodes", 1)],
    )
    monkeypatch.setattr(ds, "HybridRetriever", fake_retriever)
    pool = _FakePoolForClaimFetch(claim_rows=[
        {"id": claim_id, "name": "claim node", "subject": "s", "predicate": "p", "object": "o",
         "properties": {"truth_state": "IN"}, "scope_type": None, "scope_entity_id": None},
    ])

    result = _run(ds.search_global(
        pool=pool, query="policy", object_types=["claim"],
        scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))

    hits = result["results"]["claim"]
    assert len(hits) == 1
    assert hits[0]["id"] == str(claim_id)
    assert hits[0]["subject"] == "s"


def test_search_global_never_merges_result_types(monkeypatch):
    """The hard, deliberate design constraint (module docstring): results
    stay grouped by object_type, no cross-type ranked list is produced."""
    async def fake_find_applicable_procedures(pool, **kwargs):
        return [_procedure_row()]

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    monkeypatch.setattr(ds, "HybridRetriever", _FakeHybridRetriever())

    result = _run(ds.search_global(
        pool=object(), query="deploy", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))
    assert set(result["results"].keys()) == {"procedure", "task", "claim"}
    assert isinstance(result["results"], dict)  # grouped, not a single flat list


# ---------------------------------------------------------------------------
# find_best_way -- composes find_applicable_procedures + check_procedure_reuse
# ---------------------------------------------------------------------------

def _verdict(procedure="p", verdict="ALLOW"):
    return ProcedureVerdict(
        verdict=verdict, procedure=procedure,
        reason="all real hard constraints satisfied",
        evidence=["procedure:x"], capability_note="5 successes / 5 attempts",
    )


def test_find_best_way_honest_empty_when_no_survivors(monkeypatch):
    async def fake_find_applicable_procedures(pool, **kwargs):
        return []

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)

    result = _run(ds.find_best_way(
        pool=object(), goal="deploy safely",
        scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))
    assert result["recommendation"] is None
    assert result["alternatives"] == []
    assert result["confidence"] == "none"
    assert "honest empty result" in result["reason"]


def test_find_best_way_never_fabricates_a_recommendation_require_verified_default(monkeypatch):
    seen = {}

    async def fake_find_applicable_procedures(pool, **kwargs):
        seen["require_verified"] = kwargs["require_verified"]
        return []

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    _run(ds.find_best_way(pool=object(), goal="g", scope=AccessScope.unrestricted(), embedder=FakeEmbedder()))
    assert seen["require_verified"] is True  # recommend defaults to requiring real verification


def test_find_best_way_composes_check_procedure_reuse_for_each_candidate(monkeypatch):
    survivors = [
        _procedure_row("00000000-0000-4000-8000-000000000001", name="A"),
        _procedure_row("00000000-0000-4000-8000-000000000002", name="B"),
    ]

    async def fake_find_applicable_procedures(pool, **kwargs):
        return survivors

    calls = []

    async def fake_check_procedure_reuse(pool, *, procedure_id, current_scope, access_scope):
        calls.append(procedure_id)
        return _verdict(procedure=procedure_id)

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    monkeypatch.setattr(ds, "check_procedure_reuse", fake_check_procedure_reuse)

    result = _run(ds.find_best_way(
        pool=object(), goal="deploy", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))

    assert calls == ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]
    assert result["recommendation"]["id"] == "00000000-0000-4000-8000-000000000001"
    assert result["recommendation"]["verdict"] == "ALLOW"
    assert result["recommendation"]["capability_note"] == "5 successes / 5 attempts"
    assert len(result["alternatives"]) == 1
    assert result["confidence"] == "high"


def test_find_best_way_applies_scope_constraint_postfilter(monkeypatch):
    survivors = [
        _procedure_row("00000000-0000-4000-8000-000000000001", scope_type="repository", scope_entity_id="repo-A"),
        _procedure_row("00000000-0000-4000-8000-000000000002", scope_type="repository", scope_entity_id="repo-B"),
    ]

    async def fake_find_applicable_procedures(pool, **kwargs):
        return survivors

    async def fake_check_procedure_reuse(pool, *, procedure_id, current_scope, access_scope):
        return _verdict(procedure=procedure_id)

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    monkeypatch.setattr(ds, "check_procedure_reuse", fake_check_procedure_reuse)

    result = _run(ds.find_best_way(
        pool=object(), goal="deploy", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
        scope_constraint={"repository_id": "repo-B"},
    ))
    assert result["recommendation"]["id"] == "00000000-0000-4000-8000-000000000002"
    assert result["alternatives"] == []


def test_find_best_way_skips_a_procedure_not_found_race(monkeypatch):
    survivors = [
        _procedure_row("00000000-0000-4000-8000-000000000001"),
        _procedure_row("00000000-0000-4000-8000-000000000002"),
    ]

    async def fake_find_applicable_procedures(pool, **kwargs):
        return survivors

    async def fake_check_procedure_reuse(pool, *, procedure_id, current_scope, access_scope):
        if procedure_id == "00000000-0000-4000-8000-000000000001":
            raise ProcedureNotFound("gone")
        return _verdict(procedure=procedure_id)

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    monkeypatch.setattr(ds, "check_procedure_reuse", fake_check_procedure_reuse)

    result = _run(ds.find_best_way(
        pool=object(), goal="deploy", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
    ))
    assert result["recommendation"]["id"] == "00000000-0000-4000-8000-000000000002"


def test_find_best_way_allow_unverified_opts_out_of_require_verified(monkeypatch):
    seen = {}

    async def fake_find_applicable_procedures(pool, **kwargs):
        seen["require_verified"] = kwargs["require_verified"]
        return []

    monkeypatch.setattr(ds, "find_applicable_procedures", fake_find_applicable_procedures)
    _run(ds.find_best_way(
        pool=object(), goal="g", scope=AccessScope.unrestricted(), embedder=FakeEmbedder(),
        constraints={"allow_unverified": True},
    ))
    assert seen["require_verified"] is False


# ---------------------------------------------------------------------------
# REST routes -- app/api/search.py, via TestClient. The domain_search
# functions themselves are monkeypatched (already proven above); this
# proves request parsing, dependency wiring, and response shaping only.
# ---------------------------------------------------------------------------

def _client():
    app = FastAPI()
    app.state.pool = object()
    app.include_router(search_api.router)
    return TestClient(app)


def test_get_search_route_parses_object_types_and_calls_search_global(monkeypatch):
    captured = {}

    async def fake_search_global(pool, query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return {
            "query": query, "object_types": kwargs["object_types"] or [],
            "results": {}, "counts": {},
        }

    monkeypatch.setattr(search_api, "search_global", fake_search_global)

    resp = _client().get("/v1/search", params={
        "q": "deploy", "object_types": "procedure,claim",
        "repository_id": "repo-1", "limit": 5,
    })
    assert resp.status_code == 200
    assert captured["query"] == "deploy"
    assert captured["kwargs"]["object_types"] == ["procedure", "claim"]
    assert captured["kwargs"]["repository_id"] == "repo-1"
    assert captured["kwargs"]["limit"] == 5
    body = resp.json()
    assert body["object_types"] == ["procedure", "claim"]


def test_get_search_route_omitted_object_types_passes_none(monkeypatch):
    captured = {}

    async def fake_search_global(pool, query, **kwargs):
        captured["object_types"] = kwargs["object_types"]
        return {"query": query, "object_types": [], "results": {}, "counts": {}}

    monkeypatch.setattr(search_api, "search_global", fake_search_global)
    resp = _client().get("/v1/search", params={"q": "deploy"})
    assert resp.status_code == 200
    assert captured["object_types"] is None


def test_get_search_route_surfaces_unsupported_object_type_as_422(monkeypatch):
    async def fake_search_global(pool, query, **kwargs):
        raise ValueError("search_global: object_type 'solution' is not searchable")

    monkeypatch.setattr(search_api, "search_global", fake_search_global)
    # Orchestrator fix: the route now explicitly catches search_global's
    # ValueError and raises HTTPException(422) -- a bad request parameter
    # is a real 422, not an unhandled 500. See app/api/search.py's own
    # docstring for why the plain-raise behavior this test used to assert
    # was corrected rather than left as documented-but-false.
    resp = _client().get("/v1/search", params={"q": "x", "object_types": "solution"})
    assert resp.status_code == 422
    assert "not searchable" in resp.json()["detail"]


def test_post_recommend_route_calls_find_best_way_with_body_fields(monkeypatch):
    captured = {}

    async def fake_find_best_way(pool, goal, **kwargs):
        captured["goal"] = goal
        captured["kwargs"] = kwargs
        return {
            "goal": goal, "recommendation": None, "alternatives": [],
            "confidence": "none", "reason": "honest empty result, not a fabricated recommendation",
        }

    monkeypatch.setattr(search_api, "find_best_way", fake_find_best_way)

    resp = _client().post("/v1/search/recommend", json={
        "goal": "deploy the service safely",
        "context": {"repo": ["backend"]},
        "constraints": {"allow_unverified": True},
    })
    assert resp.status_code == 200
    assert captured["goal"] == "deploy the service safely"
    assert captured["kwargs"]["context"] == {"repo": ["backend"]}
    assert captured["kwargs"]["constraints"] == {"allow_unverified": True}
    body = resp.json()
    assert body["recommendation"] is None
    assert body["confidence"] == "none"


def test_post_recommend_route_rejects_empty_goal():
    resp = _client().post("/v1/search/recommend", json={"goal": ""})
    assert resp.status_code == 422
