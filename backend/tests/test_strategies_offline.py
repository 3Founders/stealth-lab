"""
DB-free coverage for strategies.py's two ExtractionStrategy.extract()
bodies -- previously proven only by test_procedure_extraction_strategies_e2e.py,
which needs a real DATABASE_URL. Every case here uses evidence with
project_id=None (started_at=None), the same choice that e2e file's own
LLM-path tests already make: derive_preconditions/derive_scope both
short-circuit before ever touching the pool for evidence shaped this way
(proven directly in test_derive_offline.py), so a `pool` argument that
raises if touched is enough to prove no hidden DB dependency exists --
no FakePool needed here at all.

FakeClient is the same scripted-response convention
test_procedure_extraction_strategies_e2e.py and test_htn_agent.py use.
"""
import asyncio
import types

import pytest

from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.procedure_extraction.strategies import (
    DeterministicExtractor,
    GroundedHybridExtractor,
)


class PoolThatMustNotBeTouched:
    async def fetch(self, *a, **kw):
        raise AssertionError("must not reach the pool -- evidence has no project_id")


def _run(coro):
    return asyncio.run(coro)


def _evidence(tool_sequence=("Read", "Read", "Edit", "Bash")):
    return ProcedureEvidence(
        goal_text="fix the failing login test",
        outcome="success",
        tool_sequence=list(tool_sequence),
        observations=[
            {"observation_type": "file_touched", "properties": {"file_path": "auth/login.py"}},
            {"observation_type": "test_run", "properties": {"passed": True}},
        ],
    )


class FakeClient:
    def __init__(self, script=None, raises=False):
        self.script = list(script or [])
        self.requests = []
        self._raises = raises
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if self._raises:
            raise self._raises if isinstance(self._raises, Exception) else RuntimeError("upstream call failed")
        content = self.script.pop(0) if self.script else '{"abstain": true}'
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])


# --- DeterministicExtractor.extract ---

def test_deterministic_extractor_produces_a_literal_procedure_offline():
    ev = _evidence()
    proc = _run(DeterministicExtractor().extract(PoolThatMustNotBeTouched(), ev))
    assert len(proc.steps) == 3  # Read x2, Edit, Bash -> 3 run-length groups
    assert proc.capability_statement == ev.goal_text[:200]
    assert proc.preconditions == []  # no project_id -- honestly empty, not fabricated


# --- GroundedHybridExtractor.extract: ExtractionTransientFailure paths ---
#
# No silent degrade-to-deterministic anymore: every one of these must
# raise ExtractionTransientFailure and leave no result at all, so a
# caller can never mistake a fallback for a real abstraction.

def test_grounded_hybrid_raises_with_no_client():
    ev = _evidence()
    with pytest.raises(ExtractionTransientFailure):
        _run(GroundedHybridExtractor(None).extract(PoolThatMustNotBeTouched(), ev))


def test_grounded_hybrid_abstains_with_no_skeleton_even_with_a_client():
    """`not skeleton` returns None (a genuine abstain -- nothing to
    summarize is a structural fact about the evidence, not something a
    retry fixes), WITHOUT ever calling the model."""
    ev = _evidence(tool_sequence=[])
    client = FakeClient(['{"capability_statement": "x", "step_phrases": ["y"]}'])
    result = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert result is None
    assert client.requests == [], "must never call the model over an empty skeleton"


def test_grounded_hybrid_raises_transient_failure_when_the_client_call_raises():
    ev = _evidence()
    client = FakeClient(raises=True)
    with pytest.raises(ExtractionTransientFailure) as excinfo:
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert excinfo.value.is_rate_limit is False


def test_grounded_hybrid_raises_transient_failure_marked_rate_limit_on_a_429():
    ev = _evidence()
    client = FakeClient(raises=RuntimeError("Error code: 429 - rate_limit_exceeded"))
    with pytest.raises(ExtractionTransientFailure) as excinfo:
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert excinfo.value.is_rate_limit is True


def test_grounded_hybrid_raises_transient_failure_on_a_malformed_response():
    ev = _evidence()
    client = FakeClient(["this is not the expected format at all"])
    with pytest.raises(ExtractionTransientFailure):
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))


def test_grounded_hybrid_raises_transient_failure_on_step_count_mismatch():
    ev = _evidence()  # skeleton has 3 groups: Read, Edit, Bash
    client = FakeClient(['{"capability_statement": "do a thing", "step_phrases": ["only one step"]}'])
    with pytest.raises(ExtractionTransientFailure, match="did not parse"):
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))


def test_grounded_hybrid_raises_transient_failure_on_json_wrapped_in_prose():
    """A response that isn't ONLY the JSON object (leading prose, no code
    fence) is still a parse failure -- the fence-stripping in
    _parse_abstraction_response only handles a ```-wrapped block, not
    arbitrary surrounding text, so this must raise like any other
    malformed response rather than silently succeed on a lucky substring
    match."""
    ev = _evidence()
    client = FakeClient(['Sure, here you go: {"capability_statement": "c", "step_phrases": ["a", "b", "c"]}'])
    with pytest.raises(ExtractionTransientFailure):
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))


def test_grounded_hybrid_returns_none_on_explicit_abstain():
    """A genuine {"abstain": true} response is the model's real, final
    answer -- distinct from a parse failure, never raises."""
    ev = _evidence()
    client = FakeClient(['{"abstain": true}'])
    result = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert result is None


# --- GroundedHybridExtractor.extract: the real abstraction path ---

def test_grounded_hybrid_uses_llm_output_when_well_formed():
    ev = _evidence()
    client = FakeClient([
        '{"capability_statement": "locate the failing test\'s source file and apply a targeted fix", '
        '"step_phrases": ["read the relevant files", "apply a fix", "run the test suite"]}',
    ])
    proc = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))

    assert "locate the failing test" in proc.capability_statement
    assert len(proc.steps) == 3
    assert [s.goal for s in proc.steps] == [
        "read the relevant files", "apply a fix", "run the test suite",
    ]
    # allowed_implementations still carries the real tool name structurally,
    # even though the phrasing itself came from the model.
    assert proc.steps[0].allowed_implementations == [{"type": "tool", "name": "Read"}]
    assert len(client.requests) == 1
    assert client.requests[0]["model"] == "gemma-4-31B-it"
    assert client.requests[0]["temperature"] == 0.2


def test_grounded_hybrid_accepts_a_code_fenced_json_response():
    """Some OpenAI-compatible providers wrap JSON in a ```json ... ```
    fence even when told not to -- a real, observed response shape, so
    this must parse successfully rather than being treated as malformed."""
    ev = _evidence()
    client = FakeClient([
        '```json\n{"capability_statement": "c", "step_phrases": ["a", "b", "c"]}\n```',
    ])
    proc = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert proc.capability_statement == "c"
    assert [s.goal for s in proc.steps] == ["a", "b", "c"]


def test_grounded_hybrid_threads_model_and_temperature_to_the_call():
    ev = _evidence()
    client = FakeClient(['{"capability_statement": "c", "step_phrases": ["a", "b", "c"]}'])
    _run(GroundedHybridExtractor(client, model="a-model", temperature=0.9).extract(
        PoolThatMustNotBeTouched(), ev,
    ))
    assert client.requests[0]["model"] == "a-model"
    assert client.requests[0]["temperature"] == 0.9
