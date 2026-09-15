"""
Pure, DB-free coverage for app/services/semantic_decomposition.py --
the step-level PROPOSITION/ABSTRACT_ACTION/CONCRETE_IMPLEMENTATION/
VERIFICATION/CONTEXT_ONLY classifier wired into
skill_ingestion.py::compile_skill_artifact (filters CONTEXT_ONLY/
PROPOSITION out of a procedure's own stored steps). Same FakeClient
convention as test_strategies_offline.py / test_implementation_goals_offline.py.
"""
import asyncio
import types

import pytest

from app.services.semantic_decomposition import (
    STEP_SEMANTIC_KINDS,
    SemanticDecompositionTransientFailure,
    classify_step_deterministic,
    classify_step_semantics,
    decompose_steps,
)


def _run(coro):
    return asyncio.run(coro)


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


# --- deterministic path ---

def test_classify_step_deterministic_catches_the_real_template_placeholder():
    """The exact real corpus shape this pass exists for: writing-plans'
    own `Create: `exact/path/to/file.py`` template line."""
    assert classify_step_deterministic("Create: `exact/path/to/file.py`") == "CONTEXT_ONLY"


def test_classify_step_deterministic_returns_none_for_ordinary_steps():
    assert classify_step_deterministic("Design units with clear boundaries.") is None


# --- classify_step_semantics: deterministic-first, safe defaults ---

def test_classify_step_semantics_heuristic_for_template_placeholder():
    result = _run(classify_step_semantics("Modify: `exact/path/to/existing.py:123-145`"))
    assert result == {"kind": "CONTEXT_ONLY", "classification": "heuristic"}


def test_classify_step_semantics_unclassified_default_with_no_client():
    """No deterministic signal, no client -- ABSTRACT_ACTION (keep the
    step), never a fabricated filter decision."""
    result = _run(classify_step_semantics("Design units with clear boundaries."))
    assert result == {"kind": "ABSTRACT_ACTION", "classification": "unclassified"}


def test_classify_step_semantics_deterministic_match_skips_the_client_entirely():
    client = FakeClient(['{"kind": "PROPOSITION"}'])
    result = _run(classify_step_semantics("Create: `exact/path/to/file.py`", client=client))
    assert result["kind"] == "CONTEXT_ONLY"
    assert client.requests == []


def test_classify_step_semantics_llm_classified_on_success():
    client = FakeClient(['{"kind": "VERIFICATION"}'])
    result = _run(classify_step_semantics(
        "Run the tests and make sure they pass.", client=client,
        skill_purpose="Test-driven development.",
    ))
    assert result == {"kind": "VERIFICATION", "classification": "llm_classified"}
    assert "Test-driven development." in client.requests[0]["messages"][1]["content"]


def test_classify_step_semantics_context_only_via_llm():
    """The real motivating case: a document's own anti-pattern warning
    list, misparsed as a step by the structural parser."""
    client = FakeClient(['{"kind": "CONTEXT_ONLY"}'])
    result = _run(classify_step_semantics(
        "\"TBD\", \"TODO\", \"implement later\", \"fill in details\"", client=client,
    ))
    assert result["kind"] == "CONTEXT_ONLY"


def test_classify_step_semantics_safe_default_on_genuine_abstain():
    client = FakeClient(['{"abstain": true}'])
    result = _run(classify_step_semantics("Some ambiguous step.", client=client))
    assert result == {"kind": "ABSTRACT_ACTION", "classification": "unclassified"}


def test_classify_step_semantics_safe_default_on_transient_failure():
    """No silent fallback in the direction that would lose content: an
    LLM error must NEVER filter a step out -- it keeps ABSTRACT_ACTION
    and reports needs_enrichment, same discipline
    implementation_goals.py already established."""
    client = FakeClient(raises=True)
    result = _run(classify_step_semantics("Some step.", client=client))
    assert result == {"kind": "ABSTRACT_ACTION", "classification": "needs_enrichment"}


def test_classify_step_semantics_safe_default_on_malformed_response():
    client = FakeClient(["not json at all"])
    result = _run(classify_step_semantics("Some step.", client=client))
    assert result["kind"] == "ABSTRACT_ACTION"
    assert result["classification"] == "needs_enrichment"


def test_classify_step_semantics_rejects_an_invalid_kind_as_a_parse_failure():
    """A model inventing a sixth category is exactly as invalid as
    malformed JSON -- Pydantic's own field_validator catches it, and the
    caller treats it identically (safe default, needs_enrichment)."""
    client = FakeClient(['{"kind": "NOT_A_REAL_KIND"}'])
    result = _run(classify_step_semantics("Some step.", client=client))
    assert result["classification"] == "needs_enrichment"


# --- decompose_steps: the real batch entry point compile_skill_artifact calls ---

def test_decompose_steps_filters_context_only_and_proposition():
    steps = [
        "Design units with clear boundaries.",
        "Create: `exact/path/to/file.py`",
        "This skill applies to Python projects using pytest.",
    ]
    client = FakeClient([
        '{"kind": "ABSTRACT_ACTION"}',  # step 1 (no deterministic match, so this IS called)
        '{"kind": "PROPOSITION"}',      # step 3 (step 2 resolves deterministically, no call)
    ])
    kept, report = _run(decompose_steps(steps, client=client))
    assert kept == ["Design units with clear boundaries."]
    assert report == {
        "total": 3, "kept": 1, "filtered": 2,
        "by_kind": {
            "PROPOSITION": 1, "ABSTRACT_ACTION": 1, "CONCRETE_IMPLEMENTATION": 0,
            "VERIFICATION": 0, "CONTEXT_ONLY": 1,
        },
        "errors": 0,
    }


def test_decompose_steps_preserves_order():
    steps = ["Step one.", "Step two.", "Step three."]
    kept, report = _run(decompose_steps(steps, client=None))
    assert kept == steps
    assert report["filtered"] == 0


def test_decompose_steps_never_empties_a_real_step_list():
    """Guard against an overzealous classifier: if EVERY step somehow
    gets filtered, fall back to the original list rather than producing
    a zero-step procedure."""
    steps = ["Create: `exact/path/to/a.py`", "Create: `exact/path/to/b.py`"]
    kept, report = _run(decompose_steps(steps, client=None))
    assert kept == steps, "both matched CONTEXT_ONLY deterministically -- the empty-result guard must restore them"
    # The report reflects what was ACTUALLY persisted (post-guard), not a
    # hypothetical -- both were classified CONTEXT_ONLY (visible in
    # by_kind), but the guard means nothing was really filtered out.
    assert report["by_kind"]["CONTEXT_ONLY"] == 2
    assert report["filtered"] == 0
    assert report["kept"] == 2


def test_decompose_steps_counts_errors_without_losing_the_step():
    """An unexpected exception inside classification (not the tracked
    SemanticDecompositionTransientFailure -- something classify_step_
    semantics itself doesn't catch) must still keep the step and count
    as a real, visible error."""
    import app.services.semantic_decomposition as sd

    async def _boom(*a, **kw):
        raise ValueError("unexpected")

    original = sd.classify_step_semantics
    sd.classify_step_semantics = _boom
    try:
        kept, report = _run(decompose_steps(["A step."], client=None))
    finally:
        sd.classify_step_semantics = original
    assert kept == ["A step."]
    assert report["errors"] == 1


def test_step_semantic_kinds_are_exactly_the_founder_directive_vocabulary():
    assert set(STEP_SEMANTIC_KINDS) == {
        "PROPOSITION", "ABSTRACT_ACTION", "CONCRETE_IMPLEMENTATION", "VERIFICATION", "CONTEXT_ONLY",
    }


def test_classify_via_llm_raises_transient_failure_directly():
    from app.services.semantic_decomposition import _classify_step_via_llm

    client = FakeClient(raises=True)
    with pytest.raises(SemanticDecompositionTransientFailure):
        _run(_classify_step_via_llm(client, "a-model", step_text="x", skill_purpose=None))
