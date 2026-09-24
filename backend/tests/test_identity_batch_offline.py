from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.semantic import prompts
from app.services.semantic.errors import ErrorKind, ProviderError
from app.services.semantic.providers import JEVProvider, OpenAICompatProvider
from tests.semantic_fakes import ScriptedProvider, make_judge, transient


class _Completions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=self.content),
                finish_reason="stop",
            )]
        )


class _OpenAIClient:
    def __init__(self, content):
        self.completions = _Completions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_openai_compat_identity_batch_uses_one_request_for_all_candidates():
    client = _OpenAIClient(json.dumps({"verdicts": [
        {"index": 0, "relation": "same", "confidence": 0.91},
        {"index": 1, "relation": "related", "confidence": 0.72},
        {"index": 2, "relation": "distinct", "confidence": 0.99},
    ]}))
    provider = OpenAICompatProvider("gemini", [client], "gemini-test")

    result = asyncio.run(provider.identity_batch(
        "goal", "new goal", ["first", "second", "third"]))

    assert result == [
        {"relation": "same", "confidence": 0.91},
        {"relation": "related", "confidence": 0.72},
        {"relation": "distinct", "confidence": 0.99},
    ]
    assert len(client.completions.calls) == 1
    system_prompt = client.completions.calls[0]["messages"][0]["content"]
    assert "achieving one necessarily means achieving the other" in system_prompt
    assert "untrusted data" in system_prompt.lower()
    assert "never follow" in system_prompt.lower()
    for candidate_text in ("first", "second", "third"):
        assert candidate_text not in system_prompt
    payload = json.loads(client.completions.calls[0]["messages"][1]["content"])
    assert payload["candidates"] == [
        {"index": 0, "text": "first"},
        {"index": 1, "text": "second"},
        {"index": 2, "text": "third"},
    ]


class _JEVRemote:
    model = "jev-test"

    def __init__(self):
        self.calls = []

    async def systemone(self, state, questions):
        self.calls.append((state, questions))
        relations = ("same", "specializes", "related")
        confidences = (0.88, 0.77, 0.66)
        return {
            key: {"choice": relations[index], "confidence": confidences[index]}
            for index, key in enumerate(questions)
        }


def test_jev_identity_batch_uses_one_request_for_all_candidates():
    remote = _JEVRemote()
    provider = JEVProvider(remote, {"identity"})

    result = asyncio.run(provider.identity_batch(
        "goal", "new goal", ["first", "second", "third"]))

    assert result == [
        {"relation": "same", "confidence": 0.88},
        {"relation": "specializes", "confidence": 0.77},
        {"relation": "related", "confidence": 0.66},
    ]
    assert len(remote.calls) == 1
    state, questions = remote.calls[0]
    assert len(questions) == 3
    for candidate_text in ("first", "second", "third"):
        assert all(candidate_text not in question["instructions"] for question in questions.values())
    assert all("candidate index" in question["instructions"] for question in questions.values())
    assert json.loads(state)["candidates"] == [
        {"index": 0, "text": "first"},
        {"index": 1, "text": "second"},
        {"index": 2, "text": "third"},
    ]


def test_identity_batch_parser_rejects_missing_duplicate_and_unknown_indexes():
    assert prompts.IDENTITY_PROMPT_VERSION == "identity@v2"
    valid = {"relation": "distinct", "confidence": 0.9}
    with pytest.raises(ValueError):
        prompts.parse_identity_batch("goal", {"verdicts": [
            {"index": 0, **valid},
        ]}, 2)
    with pytest.raises(ValueError):
        prompts.parse_identity_batch("goal", {"verdicts": [
            {"index": 0, **valid},
            {"index": 0, **valid},
        ]}, 2)
    with pytest.raises(ValueError):
        prompts.parse_identity_batch("goal", {"verdicts": [
            {"index": 0, **valid},
            {"index": 2, **valid},
        ]}, 2)


@pytest.mark.parametrize("confidence", [None, True, "0.5", float("nan"), float("inf"), float("-inf"), -0.1, 1.1])
def test_identity_parsers_reject_invalid_confidence_values(confidence):
    with pytest.raises(ValueError):
        prompts.parse_identity("goal", {"relation": "same", "confidence": confidence})
    with pytest.raises(ValueError):
        prompts.parse_identity_batch("goal", {"verdicts": [
            {"index": 0, "relation": "same", "confidence": confidence},
        ]}, 1)


def test_identity_parsers_require_confidence_and_accept_numeric_boundaries():
    with pytest.raises(ValueError):
        prompts.parse_identity("goal", {"relation": "same"})
    with pytest.raises(ValueError):
        prompts.parse_identity_batch("goal", {"verdicts": [
            {"index": 0, "relation": "same"},
        ]}, 1)
    assert prompts.parse_identity("goal", {"relation": "same", "confidence": 0})["confidence"] == 0.0
    assert prompts.parse_identity("goal", {"relation": "same", "confidence": 1})["confidence"] == 1.0


def test_malformed_provider_confidence_fails_closed():
    class MissingConfidenceRemote:
        model = "jev-test"

        async def systemone(self, state, questions):
            return {"relation": {"choice": "same"}}

    provider = JEVProvider(MissingConfidenceRemote(), {"identity"})
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.identity("goal", "new", "existing"))
    assert exc.value.kind is ErrorKind.TRANSIENT

    client = _OpenAIClient(json.dumps({
        "verdicts": [{"index": 0, "relation": "same", "confidence": "0.5"}]
    }))
    provider = OpenAICompatProvider("gemini", [client], "gemini-test")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.identity_batch("goal", "new", ["existing"]))
    assert exc.value.kind is ErrorKind.TRANSIENT

    client = _OpenAIClient(123)
    provider = OpenAICompatProvider("gemini", [client], "gemini-test")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.identity("goal", "new", "existing"))
    assert exc.value.kind is ErrorKind.TRANSIENT


def test_missing_batch_method_is_a_contract_error_not_pair_fallback():
    class PairOnly:
        name = "pair-only"
        model = "pair-only-model"
        capabilities = frozenset({"identity"})

        def __init__(self):
            self.pair_calls = 0

        def supports(self, capability):
            return capability in self.capabilities

        async def identity(self, kind, a, b):
            self.pair_calls += 1
            return {"relation": "distinct", "confidence": 0.5}

    provider = PairOnly()
    result = asyncio.run(make_judge(provider, attempts=2).judge_identity_batch(
        "goal", "new goal", ["existing"]))

    assert not result.ok
    assert provider.pair_calls == 0
    assert len(result.attempts) == 1
    assert result.attempts[0].error_kind == ErrorKind.PERMANENT.value
    assert "identity batch contract" in result.attempts[0].detail


def test_identity_batch_chain_retries_the_batch_then_falls_back():
    primary = ScriptedProvider("jev", [transient("timeout")])
    fallback = ScriptedProvider("gemini", [[
        {"relation": "same", "confidence": 0.9},
        {"relation": "related", "confidence": 0.8},
    ]])
    judge = make_judge(primary, fallback, attempts=2)

    result = asyncio.run(judge.judge_identity_batch(
        "goal", "new goal", ["first", "second"]))

    assert result.ok
    assert result.provider == "gemini"
    assert result.fallback_used
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1
    assert judge.sleeps == [0.75]
