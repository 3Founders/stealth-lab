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
            raise RuntimeError("upstream call failed")
        content = self.script.pop(0) if self.script else "ABSTAIN"
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


# --- GroundedHybridExtractor.extract: fallback paths ---

def test_grounded_hybrid_falls_back_with_no_client():
    ev = _evidence()
    proc = _run(GroundedHybridExtractor(None).extract(PoolThatMustNotBeTouched(), ev))
    assert proc.capability_statement == ev.goal_text[:200]


def test_grounded_hybrid_skips_the_model_call_with_no_skeleton_even_with_a_client():
    """`not skeleton` short-circuits BEFORE the LLM call either way -- but
    this is a genuine dead end, not a graceful fallback: schema.py's
    ExtractedProcedure refuses zero steps unconditionally (ticket
    steps_not_empty), so DeterministicExtractor cannot produce a valid
    result from an empty tool_sequence either. Both extractors raise
    identically here; the guard only saves a wasted model call, it does
    not avoid the crash. DISCLOSED, not fixed by this pass: evidence with
    real observations but an empty tool_sequence (possible per
    evidence.py -- observations and tool_sequence are independently
    populated) reaches extract_procedure()'s V5 pre-check (which only
    inspects has_observations(), not tool_sequence) and would propagate
    this same ValidationError uncaught -- a pre-existing gap in that
    pre-check, out of scope for a coverage-only pass."""
    ev = _evidence(tool_sequence=[])
    client = FakeClient(["CAPABILITY: x\nSTEPS: y"])
    with pytest.raises(Exception, match="zero steps"):
        _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert client.requests == [], "must never call the model over an empty skeleton"


def test_grounded_hybrid_falls_back_when_the_client_call_raises():
    ev = _evidence()
    client = FakeClient(raises=True)
    proc = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert proc.capability_statement == ev.goal_text[:200]


def test_grounded_hybrid_falls_back_on_a_malformed_response():
    ev = _evidence()
    client = FakeClient(["this is not the expected format at all"])
    proc = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert proc.capability_statement == ev.goal_text[:200]


def test_grounded_hybrid_falls_back_on_step_count_mismatch():
    ev = _evidence()  # skeleton has 3 groups: Read, Edit, Bash
    client = FakeClient(["CAPABILITY: do a thing\nSTEPS: only one step"])
    proc = _run(GroundedHybridExtractor(client).extract(PoolThatMustNotBeTouched(), ev))
    assert proc.capability_statement == ev.goal_text[:200]


# --- GroundedHybridExtractor.extract: the real abstraction path ---

def test_grounded_hybrid_uses_llm_output_when_well_formed():
    ev = _evidence()
    client = FakeClient([
        "CAPABILITY: locate the failing test's source file and apply a targeted fix\n"
        "STEPS: read the relevant files; apply a fix; run the test suite",
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


def test_grounded_hybrid_threads_model_and_temperature_to_the_call():
    ev = _evidence()
    client = FakeClient(["CAPABILITY: c\nSTEPS: a; b; c"])
    _run(GroundedHybridExtractor(client, model="a-model", temperature=0.9).extract(
        PoolThatMustNotBeTouched(), ev,
    ))
    assert client.requests[0]["model"] == "a-model"
    assert client.requests[0]["temperature"] == 0.9
