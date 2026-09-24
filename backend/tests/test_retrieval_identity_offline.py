"""Offline (no DB, no network) unit tests for the pure parts of the canonical
retrieval / identity / ingestion-queue code."""
import asyncio
import math

import pytest

from app.ingestion import queue as q
from app.ingestion.config import WorkerConfig, validate_startup
from app.ingestion.worker import is_retryable
from app.services.identity_resolution import fts_or_query, rrf_fuse
from app.services.retrieval_service import (
    LocalClaim, RetrievalConfig, RetrievalMeta, _select, build_query_context, pareto_front, wilson_lcb,
)
from app.services.search_projection import build_projection
from app.services.semantic import prompts
from app.services.semantic.errors import SemanticJudgmentUnavailable
from tests.identity_fakes import ConceptEmbedder


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- candidate machinery

def test_fts_query_is_or_joined_deduplicated_and_stopword_free():
    assert fts_or_query("Find the callers of a function; find callers!") == "find | callers | function"
    assert fts_or_query("the of a") is None
    assert fts_or_query("x'; drop table goals; --") == "x | drop | table | goals"      # only [a-z0-9] tokens survive


def test_rrf_rewards_agreement_between_legs():
    s = rrf_fuse(["a", "b", "c"], ["c", "a", "d"])
    assert s["a"] > s["b"] and s["a"] > s["d"] and s["c"] > s["b"]
    assert rrf_fuse([], []) == {}


def test_wilson_lower_bound_orders_evidence_sensibly():
    assert wilson_lcb(0, 0) is None
    assert wilson_lcb(2, 2) < wilson_lcb(38, 40)               # 2/2 is weaker evidence than 38/40
    assert 0 <= wilson_lcb(0, 10) < 0.05 and wilson_lcb(10, 10) < 1.0


def test_pareto_front_keeps_non_dominated_and_treats_none_as_worst():
    a = {"n": "a", "app": 0.9, "ev": 0.2}
    b = {"n": "b", "app": 0.5, "ev": 0.8}
    c = {"n": "c", "app": 0.4, "ev": 0.1}                       # dominated by both
    d = {"n": "d", "app": 0.95, "ev": None}                     # unproven but most applicable: not dominated
    front = pareto_front([a, b, c, d], ("app", "ev"))
    assert {x["n"] for x in front} == {"a", "b", "d"}


def _item(pid, app, ev_lcb, judged=True, rel="applies"):
    return {"procedure_id": pid, "applicability_score": app, "evidence_lcb": ev_lcb, "judged": judged, "relation": rel,
            "rrf": 0.01, "confidence": app}


def test_selection_never_invents_a_winner():
    cfg = RetrievalConfig()
    m = RetrievalMeta(mode="jev")
    none_ev = _select([_item("a", 0.9, None), _item("b", 0.8, None)], m, cfg)
    assert none_ev.selected is None and "no winner invented" in none_ev.selection_reason and len(none_ev.alternatives) == 2
    unjudged = _select([_item("a", None, 0.9, judged=False, rel=None)], RetrievalMeta(mode="candidates_only"), cfg)
    assert unjudged.selected is None and unjudged.ranked and unjudged.frontier == []
    won = _select([_item("a", 0.9, 0.3), _item("b", 0.9, 0.7)], m, cfg)
    assert won.selected["procedure_id"] == "b"


def test_local_claim_working_set_is_bounded_and_relevance_ordered():
    claims = [LocalClaim(f"c{i}", f"unrelated fact {i}") for i in range(500)] + [LocalClaim("hit", "the database is postgres")]
    ctx = run(build_query_context("migrate the database schema", claims, cfg=RetrievalConfig(local_claim_budget=5)))
    assert len(ctx.claims) == 5 and "hit" in ctx.claim_ids and ctx.dropped_claims == 496
    assert ctx.text.startswith("migrate the database schema") and "postgres" in ctx.text
    empty = run(build_query_context("just a query", []))
    assert empty.text == "just a query" and empty.claim_ids == []


def test_local_claims_use_embedding_relevance_when_available():
    claims = [LocalClaim("a", "we deploy on fridays"), LocalClaim("b", "locate usages of a function")]
    ctx = run(build_query_context("find callers of a function", claims, embedder=ConceptEmbedder(), cfg=RetrievalConfig(local_claim_budget=1)))
    assert ctx.claim_ids == ["b"]                                # concept vectors: usages ~ callers


# ------------------------------------------------------------------- projections

def _goal_row(**o):
    r = dict(id="g1", canonical_name="find callers", description="d", aliases=["locate callers"], status="active", version=2,
             visibility="private", owner_id="u1", scope_type="user", scope_entity_id="u1", embedding=None, embedding_model_id=None,
             embedding_provider=None)
    return {**r, **o}


def test_projection_copies_scope_and_never_publishes_private_rows():
    p = build_projection("goal", _goal_row(), "K007")
    assert (p["visibility"], p["owner_id"], p["scope_type"], p["home_shard_id"]) == ("private", "u1", "user", "K007")
    assert "locate callers" in p["search_text"] and p["embedding"] is None and p["embedding_dim"] is None


def test_projection_stamps_embedding_model_and_version_when_a_vector_exists():
    p = build_projection("goal", _goal_row(embedding="[0.1,0.2]", embedding_model_id="gemini:x", embedding_provider="gemini"), "K000")
    assert (p["embedding_model"], p["embedding_version"], p["embedding_dim"]) == ("gemini:x", "gemini", 1024)
    q_ = build_projection("goal", _goal_row(embedding="[0.1]"), "K000")
    assert q_["embedding_model"] == "unknown"                    # never silently comparable with a real model


def test_procedure_projection_links_goal_directly():
    row = dict(id="row1", procedure_id="p1", name="n", goal="g", achieves_goal_id="gid", display_description=None,
               capability_statement=None, retrieval_document=None, preconditions=[{"description": "has git"}],
               postconditions=[], verification_state="verified", verification_stats={"attempts": 4, "successes": 3},
               availability="active", version=3, visibility="public", owner_id=None, scope_type="global", scope_entity_id=None,
               tenant_id=None, embedding=None, embedding_model_id=None, embedding_provider=None)
    p = build_projection("procedure", row, "K000")
    assert p["goal_id"] == "gid" and p["key"] == "p1" and p["procedure_row_id"] == "row1"
    assert "3/4" in p["verification_summary"] and "has git" in p["preconditions_summary"]


# --------------------------------------------------------------------- prompts

def test_identity_reply_parser_is_strict():
    assert prompts.parse_identity("goal", {"relation": "same", "confidence": 1}) == {"relation": "same", "confidence": 1.0}
    for invalid_confidence in (2, -0.1, True, "0.9", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            prompts.parse_identity("goal", {"relation": "same", "confidence": invalid_confidence})
    with pytest.raises(ValueError):
        prompts.parse_identity("goal", {"relation": "same"})
    with pytest.raises(ValueError):
        prompts.parse_identity("goal", {"relation": "contradicts", "confidence": 0.9})
    with pytest.raises(ValueError):
        prompts.parse_identity("task_procedure", {"relation": "applies_maybe"})
    assert set(prompts.IDENTITY_RELATIONS) == set(prompts.IDENTITY_SYSTEM_PROMPTS)


# ------------------------------------------------------------------ queue / worker

def test_scope_rules_for_jobs():
    q.validate_scope("ingest_candidate_bundle", "user", "private", "alice")
    for bad in (("ingest_skill_package", "user", "private", "a"), ("ingest_skill_package", "project", "public", None),
                ("x", "user", "private", None), ("x", "galaxy", "public", None), ("x", "global", "secret", None)):
        with pytest.raises(q.ScopeError):
            q.validate_scope(*bad)
    q.validate_scope("ingest_skill_package", None, None, None)          # legacy jobs: public corpus


def test_backoff_grows_caps_and_jitters():
    lo = q.backoff_seconds(3, base=30, cap=1800, rng=lambda: 0.0)
    hi = q.backoff_seconds(3, base=30, cap=1800, rng=lambda: 1.0)
    assert lo == 60 and hi == 120                                   # 30*2^2 = 120, jitter 50-100 %
    assert q.backoff_seconds(30, base=30, cap=1800, rng=lambda: 1.0) == 1800


def test_worker_config_reads_env_and_clamps(monkeypatch):
    monkeypatch.setenv("INGEST_WORKER_CONCURRENCY", "0")
    monkeypatch.setenv("INGEST_LEASE_SECONDS", "1")
    monkeypatch.setenv("INGEST_RECONCILE_GOALS", "0")
    cfg = WorkerConfig.from_env()
    assert cfg.concurrency == 1 and cfg.lease_seconds == 10 and cfg.reconcile_goals is False


def test_failure_classification():
    assert is_retryable(SemanticJudgmentUnavailable("x")) and is_retryable(ConnectionError()) and is_retryable(RuntimeError("?"))
    assert not is_retryable(KeyError("k")) and not is_retryable(ValueError("v")) and not is_retryable(q.ScopeError("s"))


def test_production_startup_refuses_to_run_without_providers(monkeypatch):
    from app.config import settings

    for name in ("jev_base_url", "gemini_api_key", "gemini_api_keys", "voyage_api_key", "local_model_name", "vertex_project"):
        monkeypatch.setattr(settings, name, None, raising=False)
    monkeypatch.setattr(settings, "use_local_models", False, raising=False)
    monkeypatch.setattr(settings, "database_url", "postgresql://x/y", raising=False)
    problems = validate_startup(strict=True)
    assert any("semantic provider" in p for p in problems) and any("embedding provider" in p for p in problems)
    assert validate_startup(strict=False) == []                     # STAGING/TEST may run without them
    monkeypatch.setattr(settings, "database_url", None, raising=False)
    monkeypatch.delenv("CONTROL_DATABASE_URL", raising=False)
    assert any("CONTROL_DATABASE_URL" in p for p in validate_startup(strict=False))
