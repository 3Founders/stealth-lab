"""
DB-free coverage for procedure_extraction/__init__.py: extract_procedure's
own orchestration logic, _select_strategy's registry-driven branching, and
evaluate_extractor's golden-set loop -- previously proven only by
test_procedure_extraction_init_e2e.py, which needs a real DATABASE_URL.

The persistence boundary (capture_procedure -- real INSERT/UPDATE
plumbing) is monkeypatched rather than faked at the SQL level, same
precedent test_registry_offline.py's own docstring sets ("pure DB-write
plumbing... deliberately left to the e2e suite, which proves real schema
compatibility a FakePool string-match cannot"): this file proves
extract_procedure's OWN decisions (which strategy, what gets passed to
capture_procedure, what the follow-up UPDATE contains, when persistence
is skipped) independent of whether capture_procedure's SQL is right.

Every FakePool below only ever answers select_extractor's own
procedure_extractors query (or evaluate_extractor's single fetchrow) --
evidence throughout carries no project_id, so derive_preconditions/
derive_scope never touch the pool at all (proven directly in
test_derive_offline.py), keeping this a one-purpose FakePool rather than
one that has to arbitrate between several real queries.
"""
import asyncio

import pytest

from app.services.procedure_extraction import (
    _select_strategy,
    evaluate_extractor,
    extract_procedure,
)
from app.services.procedure_extraction.evidence import EvidenceSource, ProcedureEvidence
from app.services.procedure_extraction.strategies import (
    DeterministicExtractor,
    GroundedHybridExtractor,
)

import types


def _run(coro):
    return asyncio.run(coro)


def _row(name="ext", version="1", kind="deterministic", config=None):
    return {
        "id": f"id-{name}-{version}", "name": name, "description": "",
        "kind": kind, "version": version, "config": config or {}, "scope": {},
    }


class FakePool:
    """Answers only select_extractor's procedure_extractors query --
    evidence in this file never carries a project_id, so derive.py's
    functions never reach the pool at all."""

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.execute_calls = []

    async def fetch(self, sql, *params):
        return self._rows

    async def execute(self, sql, *params):
        self.execute_calls.append((" ".join(sql.split()), params))


def _evidence(goal_text="fix the failing login test", outcome="success", observations=None,
              tool_sequence=("Read", "Edit")):
    return ProcedureEvidence(
        goal_text=goal_text, outcome=outcome,
        observations=observations if observations is not None else [
            {"observation_type": "file_touched", "properties": {"file_path": "auth/login.py"}},
        ],
        tool_sequence=list(tool_sequence),
    )


class FakeEvidenceSource(EvidenceSource):
    def __init__(self, evidence):
        self._evidence = evidence
        self.collect_calls = 0

    async def collect(self):
        self.collect_calls += 1
        return self._evidence


# --- extract_procedure: V5 pre-check, before any strategy runs ---

def test_failed_episode_is_refused_before_touching_the_pool_or_a_strategy():
    class PoolThatMustNotBeTouched:
        async def fetch(self, *a, **kw):
            raise AssertionError("must not be reached")

    source = FakeEvidenceSource(_evidence(outcome="failure"))
    result = _run(extract_procedure(PoolThatMustNotBeTouched(), source))
    assert result.procedure_id is None
    assert "failed episode" in result.validation_failures[0]
    assert source.collect_calls == 1


def test_observation_free_episode_is_refused_before_touching_the_pool():
    class PoolThatMustNotBeTouched:
        async def fetch(self, *a, **kw):
            raise AssertionError("must not be reached")

    source = FakeEvidenceSource(_evidence(observations=[]))
    result = _run(extract_procedure(PoolThatMustNotBeTouched(), source))
    assert result.procedure_id is None
    assert "observation-free episode" in result.validation_failures[0]


# --- extract_procedure: a strategy that produces an invalid result ---

def test_a_strategy_produced_procedure_that_fails_validation_is_reported_not_persisted(monkeypatch):
    def _must_not_be_called(*a, **kw):
        raise AssertionError("capture_procedure must not run when validation fails")
    monkeypatch.setattr(
        "app.services.procedure_extraction.capture_procedure", _must_not_be_called,
    )
    # Empty goal_text -> capability_statement == "" -> V5_evidence_sufficiency.
    source = FakeEvidenceSource(_evidence(goal_text=""))
    result = _run(extract_procedure(FakePool(rows=[]), source))
    assert result.procedure_id is None
    assert result.extracted is not None
    assert any("V5_evidence_sufficiency" in f for f in result.validation_failures)


# --- extract_procedure: dry_run skips persistence entirely ---

def test_dry_run_never_calls_capture_procedure(monkeypatch):
    def _must_not_be_called(*a, **kw):
        raise AssertionError("dry_run must never persist")
    monkeypatch.setattr(
        "app.services.procedure_extraction.capture_procedure", _must_not_be_called,
    )
    source = FakeEvidenceSource(_evidence())
    result = _run(extract_procedure(FakePool(rows=[]), source, dry_run=True))
    assert result.procedure_id is None
    assert result.extracted is not None
    assert result.extracted_by == "deterministic_v1@1"


# --- extract_procedure: the real persist path ---

def test_successful_extraction_persists_and_applies_the_migration_20_update(monkeypatch):
    captured = {}

    async def _fake_capture_procedure(pool, **kwargs):
        captured.update(kwargs)
        return {"id": "version-row-1", "procedure_id": "procedure-1"}

    monkeypatch.setattr(
        "app.services.procedure_extraction.capture_procedure", _fake_capture_procedure,
    )
    pool = FakePool(rows=[])  # no registry candidate -> deterministic fallback
    source = FakeEvidenceSource(_evidence())
    result = _run(extract_procedure(
        pool, source, owner_id="user-1", visibility="private",
    ))

    assert result.procedure_id == "procedure-1"
    assert result.version_row_id == "version-row-1"
    assert result.extracted_by == "deterministic_v1@1"
    assert captured["name"] == source._evidence.goal_text[:100]
    assert captured["owner_id"] == "user-1"
    assert captured["visibility"] == "private"

    assert len(pool.execute_calls) == 1
    sql, params = pool.execute_calls[0]
    assert "UPDATE procedures SET approval_status = 'proposed'" in sql
    assert params[0] == "version-row-1"
    assert params[2] == "deterministic_v1@1"


# --- _select_strategy: registry-driven branching ---

def test_select_strategy_falls_back_to_seeded_baseline_with_no_candidates():
    strategy, tag, allowed_binders = _run(
        _select_strategy(FakePool(rows=[]), client=None, extractor_scope=None),
    )
    assert isinstance(strategy, DeterministicExtractor)
    assert tag == "deterministic_v1@1"
    assert allowed_binders == frozenset({"literal"})


def test_select_strategy_returns_deterministic_for_a_deterministic_registry_row():
    pool = FakePool(rows=[_row(name="baseline", version="2", kind="deterministic")])
    strategy, tag, allowed_binders = _run(
        _select_strategy(pool, client=object(), extractor_scope=None),
    )
    assert isinstance(strategy, DeterministicExtractor)
    assert tag == "baseline@2"
    assert allowed_binders == frozenset(
        {"call_graph_reachable", "import_deps", "related_tests", "relevant_symbols", "literal"}
    )


def test_select_strategy_falls_back_to_deterministic_with_no_client_even_for_an_llm_row():
    """An llm-kind row is still selectable in the registry sense, but
    with no client configured there is nothing to call it with -- the
    real, registry-derived tag is kept even though the strategy itself
    degrades, so callers can see WHICH extractor was nominally chosen."""
    pool = FakePool(rows=[_row(name="challenger", version="1", kind="llm")])
    strategy, tag, allowed_binders = _run(
        _select_strategy(pool, client=None, extractor_scope=None),
    )
    assert isinstance(strategy, DeterministicExtractor)
    assert tag == "challenger@1"


def test_select_strategy_returns_grounded_hybrid_for_an_llm_row_with_a_client():
    pool = FakePool(rows=[_row(
        name="challenger", version="1", kind="llm",
        config={"model": "my-model", "temperature": 0.7, "allowed_binders": ["literal"]},
    )])
    client = object()
    strategy, tag, allowed_binders = _run(
        _select_strategy(pool, client=client, extractor_scope=None),
    )
    assert isinstance(strategy, GroundedHybridExtractor)
    assert strategy._client is client
    assert strategy._model == "my-model"
    assert strategy._temperature == 0.7
    assert tag == "challenger@1"
    assert allowed_binders == frozenset({"literal"})


# --- evaluate_extractor ---

def test_evaluate_extractor_raises_for_an_unknown_extractor_id():
    class FakeRowPool:
        async def fetchrow(self, sql, *params):
            return None

    with pytest.raises(ValueError, match="no procedure_extractors row"):
        _run(evaluate_extractor(
            FakeRowPool(), "missing-id", [], client=None, build_evidence_source=None,
        ))


def test_evaluate_extractor_skips_failed_and_observation_free_episodes():
    class FakeRowPool:
        async def fetchrow(self, sql, *params):
            return _row(name="ext", version="1", kind="deterministic")

    episodes = {
        "e-good": _evidence(),
        "e-failed": _evidence(outcome="failure"),
        "e-empty": _evidence(observations=[]),
    }
    report = _run(evaluate_extractor(
        FakeRowPool(), "ext-id", list(episodes),
        client=None, build_evidence_source=lambda eid: FakeEvidenceSource(episodes[eid]),
    ))

    assert report["golden_set_size"] == 3
    assert report["attempted"] == 1
    assert report["well_formed"] == 1
    assert report["well_formed_rate"] == pytest.approx(1.0)
    assert report["extractor"] == "ext@1"


def test_evaluate_extractor_reports_none_rate_and_empty_failures_with_nothing_attempted():
    class FakeRowPool:
        async def fetchrow(self, sql, *params):
            return _row(name="ext", version="1", kind="deterministic")

    report = _run(evaluate_extractor(
        FakeRowPool(), "ext-id", ["e-failed"],
        client=None, build_evidence_source=lambda eid: FakeEvidenceSource(_evidence(outcome="failure")),
    ))
    assert report["attempted"] == 0
    assert report["well_formed_rate"] is None
    assert report["failures_by_rule"] == {}


def test_evaluate_extractor_counts_per_rule_failures_and_uses_an_llm_strategy_when_not_deterministic():
    class FakeRowPool:
        async def fetchrow(self, sql, *params):
            return _row(name="challenger", version="1", kind="llm", config={"model": "m"})

    class FakeClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )
            self.calls = 0

        def _create(self, **kw):
            self.calls += 1
            msg = types.SimpleNamespace(content="ABSTAIN")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    client = FakeClient()
    # Empty goal_text -> capability_statement empty -> V5 failure recorded.
    report = _run(evaluate_extractor(
        FakeRowPool(), "ext-id", ["e1"],
        client=client, build_evidence_source=lambda eid: FakeEvidenceSource(_evidence(goal_text="")),
    ))
    assert report["extractor"] == "challenger@1"
    assert report["attempted"] == 1
    assert report["well_formed"] == 0
    assert report["failures_by_rule"].get("V5_evidence_sufficiency") == 1
    assert client.calls == 1  # ABSTAIN still calls the model once before falling back
