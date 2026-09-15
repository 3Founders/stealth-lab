"""
DB-free coverage for app.services.step_grounding -- the meta-harness
Sec 6-7 grounding stage. No real LLM/network call anywhere: the fake
client below is a plain Python object matching the OpenAI chat.completions
shape, same pattern test_procedure_extraction_strategies_e2e.py already
uses for GroundedHybridExtractor.
"""
from __future__ import annotations

import asyncio
import json

from app.services.step_grounding import (
    GroundedStep,
    _drop_unsupported_parameters,
    _parse_grounding_response,
    ground_step,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


class _RaisingClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("transient provider error")


# ---------------------------------------------------------------------
# ground_step -- real behavior, fake client
# ---------------------------------------------------------------------


def test_no_client_falls_back_to_raw_goal_honestly():
    result = _run(ground_step({"goal": "run migrations"}, task_description="t", client=None))
    assert result.goal == "run migrations"
    assert result.used_fallback is True


def test_no_goal_text_falls_back():
    result = _run(ground_step({}, task_description="t", client=_FakeClient("{}")))
    assert result.used_fallback is True


def test_client_error_falls_back_to_raw_goal_never_raises():
    result = _run(ground_step(
        {"goal": "regenerate bindings"}, task_description="t", client=_RaisingClient,
    ))
    assert result.goal == "regenerate bindings"
    assert result.used_fallback is True
    assert "transient provider error" in result.rationale


def test_malformed_response_falls_back():
    client = _FakeClient("not json at all")
    result = _run(ground_step({"goal": "do the thing"}, task_description="t", client=client))
    assert result.used_fallback is True
    assert result.goal == "do the thing"


def test_abstain_falls_back():
    client = _FakeClient(json.dumps({"abstain": True}))
    result = _run(ground_step({"goal": "do the thing"}, task_description="t", client=client))
    assert result.used_fallback is True


def test_supported_grounding_succeeds():
    task_description = "Modify the generated API response schema"
    claims = [{"claim_id": "C-1", "statement": "schema/api.yaml is the source-of-truth for src/generated/*"}]
    response = json.dumps({
        "goal_category": "source_of_truth_resolution",
        "parameters": {"source": "schema/api.yaml"},
        "unresolved": [],
        "abstain": False,
    })
    client = _FakeClient(response)
    result = _run(ground_step(
        {"goal": "identify source of truth"}, task_description=task_description,
        relevant_claims=claims, client=client,
    ))
    assert result.used_fallback is False
    assert result.goal == "source_of_truth_resolution"
    assert result.parameters == {"source": "schema/api.yaml"}
    assert result.unresolved == []


def test_unsupported_parameter_value_is_dropped_not_trusted():
    """Sec 7: 'must not invent file paths / commands / tools / Claims.'
    A parameter value that does not literally appear in the task
    description or any given Claim statement must be dropped, never
    silently kept as if it were grounded."""
    task_description = "Modify the generated API response schema"
    claims = [{"claim_id": "C-1", "statement": "schema/api.yaml is the source-of-truth"}]
    response = json.dumps({
        "goal_category": "code_edit",
        "parameters": {"source": "schema/api.yaml", "target": "totally/invented/path.py"},
        "unresolved": [],
        "abstain": False,
    })
    client = _FakeClient(response)
    result = _run(ground_step(
        {"goal": "edit the schema"}, task_description=task_description,
        relevant_claims=claims, client=client,
    ))
    assert result.parameters == {"source": "schema/api.yaml"}  # target dropped
    assert "target" in result.unresolved


def test_code_fenced_json_response_is_accepted():
    response = "```json\n" + json.dumps({
        "goal_category": "verification", "parameters": {}, "unresolved": [], "abstain": False,
    }) + "\n```"
    client = _FakeClient(response)
    result = _run(ground_step({"goal": "check it"}, task_description="t", client=client))
    assert result.used_fallback is False
    assert result.goal == "verification"


# ---------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------


def test_drop_unsupported_parameters_keeps_only_substrings():
    kept, dropped = _drop_unsupported_parameters(
        {"a": "found in evidence", "b": "not present"}, evidence_text="found in evidence somewhere",
    )
    assert kept == {"a": "found in evidence"}
    assert dropped == ["b"]


def test_parse_grounding_response_rejects_blank_goal_category():
    result = _parse_grounding_response(json.dumps({"goal_category": "  ", "abstain": False}))
    assert result is None
