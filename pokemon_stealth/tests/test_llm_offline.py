from __future__ import annotations

import asyncio

import pytest

from pokemon_stealth.llm import MockAgent, Usage, make_agent


def test_mock_agent_returns_scripted_responses_in_order():
    agent = MockAgent(responses=["first", "second"])
    r1 = asyncio.run(agent.respond("sys", "user", max_tokens=10))
    r2 = asyncio.run(agent.respond("sys", "user", max_tokens=10))
    assert r1.text == "first"
    assert r2.text == "second"


def test_mock_agent_raises_when_exhausted():
    agent = MockAgent(responses=["only"])
    asyncio.run(agent.respond("sys", "user", max_tokens=10))
    with pytest.raises(RuntimeError):
        asyncio.run(agent.respond("sys", "user", max_tokens=10))


def test_mock_agent_never_fabricates_usage():
    agent = MockAgent(responses=["x"])
    r = asyncio.run(agent.respond("sys", "user", max_tokens=10))
    assert r.usage.input_tokens is None
    assert r.usage.output_tokens is None


def test_usage_cost_known_model():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = usage.estimated_cost_usd("claude-sonnet-5")
    assert cost == pytest.approx(18.0)


def test_usage_cost_unknown_model_returns_none_not_zero():
    usage = Usage(input_tokens=100, output_tokens=100)
    assert usage.estimated_cost_usd("some-unpriced-model") is None


def test_usage_cost_missing_tokens_returns_none():
    usage = Usage()
    assert usage.estimated_cost_usd("claude-sonnet-5") is None


def test_make_agent_routes_claude_models_to_anthropic():
    from pokemon_stealth.llm import AnthropicAgent

    agent = make_agent("claude-sonnet-5")
    assert isinstance(agent, AnthropicAgent)


def test_make_agent_routes_unknown_models_to_openai_compat():
    from pokemon_stealth.llm import OpenAICompatAgent

    agent = make_agent("some-local-model")
    assert isinstance(agent, OpenAICompatAgent)
