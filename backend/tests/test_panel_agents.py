"""
Offline tests for AnthropicAgent/OpenAICompatAgent (MVP plan, Section 7).

What these can and cannot verify: they mock the SDK client objects, so
they confirm this code correctly extracts text from a well-formed SDK
response and correctly raises when a required key is missing. They
prove nothing about whether real API calls succeed, what real models
actually output, or account/network/auth behavior -- that requires
live credentials this environment doesn't have. Genuinely closeable
offline is "does our wrapper code handle the SDK's response shape
correctly"; genuinely not closeable offline is "do real models produce
output our JSON extraction can parse." The latter stays an open risk
until first real usage.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.debate.panel import AnthropicAgent, OpenAICompatAgent, _extract_json


def test_anthropic_agent_extracts_text_from_sdk_response():
    """Mocks anthropic.AsyncAnthropic at the point AnthropicAgent imports it."""
    fake_block = SimpleNamespace(type="text", text="hello from claude")
    fake_response = SimpleNamespace(content=[fake_block])

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        agent = AnthropicAgent(agent_id="a")
        with patch("app.debate.panel.settings") as mock_settings:
            mock_settings.require.return_value = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-6"
            result = asyncio.run(agent.respond("system", "user"))

    assert result == "hello from claude"
    mock_client.messages.create.assert_called_once()


def test_anthropic_agent_ignores_non_text_blocks():
    """A real response can mix text and tool_use blocks; only text should concatenate."""
    blocks = [
        SimpleNamespace(type="tool_use", text=None),
        SimpleNamespace(type="text", text="part one "),
        SimpleNamespace(type="text", text="part two"),
    ]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=SimpleNamespace(content=blocks))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        agent = AnthropicAgent(agent_id="a")
        with patch("app.debate.panel.settings") as mock_settings:
            mock_settings.require.return_value = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-6"
            result = asyncio.run(agent.respond("system", "user"))

    assert result == "part one part two"


def test_openai_compat_agent_extracts_message_content():
    """Covers both the OpenAI seat and the Fireworks/Kimi seat -- same code path."""
    fake_choice = SimpleNamespace(message=SimpleNamespace(content="hello from kimi"))
    fake_response = SimpleNamespace(choices=[fake_choice])

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        agent = OpenAICompatAgent(
            agent_id="b", model_id="some-model", family="moonshot",
            api_key_field="fireworks_api_key", base_url="https://api.fireworks.ai/inference/v1",
        )
        with patch("app.debate.panel.settings") as mock_settings:
            mock_settings.require.return_value = "fake-key"
            result = asyncio.run(agent.respond("system", "user"))

    assert result == "hello from kimi"


def test_openai_compat_agent_handles_none_content():
    """A tool-call-only response has message.content = None; must not crash."""
    fake_choice = SimpleNamespace(message=SimpleNamespace(content=None))
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[fake_choice])
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        agent = OpenAICompatAgent(
            agent_id="b", model_id="m", family="openai", api_key_field="openai_api_key",
        )
        with patch("app.debate.panel.settings") as mock_settings:
            mock_settings.require.return_value = "fake-key"
            result = asyncio.run(agent.respond("system", "user"))

    assert result == ""  # empty, not a crash


# --- _extract_json against realistic model-output quirks, not just clean fixtures ---

def test_extract_json_survives_trailing_commentary():
    text = '{"action": "pass", "content": "ok"}\n\nLet me know if you need anything else!'
    assert _extract_json(text)["action"] == "pass"


def test_extract_json_survives_leading_commentary_and_fence():
    text = 'Here is my response:\n\n```json\n{"action": "propose", "summary": "s"}\n```'
    assert _extract_json(text)["action"] == "propose"


def test_extract_json_handles_nested_objects_in_change_set():
    """A real change_set has nested dicts; naive first-brace/last-brace slicing must not break."""
    text = '''{"action": "propose", "change_set": {"ops": [{"op_type": "update_task_node",
              "changes": {"io_schema": {"type": "object", "properties": {"x": {"type": "string"}}}}}]}}'''
    parsed = _extract_json(text)
    assert parsed["change_set"]["ops"][0]["changes"]["io_schema"]["type"] == "object"


def test_extract_json_rejects_single_quoted_pseudo_json():
    """Python-dict-style output (single quotes) is not valid JSON and must fail loudly."""
    with pytest.raises(ValueError):
        _extract_json("{'action': 'pass'}")


def test_extract_json_picks_first_fenced_block_when_multiple_present():
    text = 'reasoning...\n```json\n{"action": "pass"}\n```\nmore text\n```\nnot json\n```'
    assert _extract_json(text)["action"] == "pass"


# --- Local provider support (development without paid API access) ---

def test_local_panel_derives_distinct_families():
    """
    Family must come from the model name, so llama3.2 and llama3.1 would
    correctly count as the same family (shared pretraining lineage) while
    llama/qwen/mistral count as distinct.
    """
    from app.config import get_settings
    from app.debate.panel import assert_heterogeneous, local_panel

    with patch("app.debate.panel.settings") as s:
        s.local_panel_models = "llama3.2,qwen2.5,mistral"
        s.local_base_url = "http://localhost:11434/v1"
        panel = local_panel()

    assert [a.family for a in panel] == ["llama", "qwen", "mistral"]
    assert_heterogeneous(panel)  # must not raise


def test_local_panel_catches_same_family_different_versions():
    """Two Llama versions share blind spots and must fail the check."""
    from app.debate.panel import assert_heterogeneous, local_panel

    with patch("app.debate.panel.settings") as s:
        s.local_panel_models = "llama3.2,llama3.1"
        s.local_base_url = "http://localhost:11434/v1"
        panel = local_panel()

    with pytest.raises(ValueError, match="not heterogeneous"):
        assert_heterogeneous(panel)


def test_local_agent_needs_no_api_key():
    """
    A local server requires no credential. settings.require() must not be
    called for it -- that would raise and make local mode unusable.
    """
    from app.debate.panel import OpenAICompatAgent

    fake_choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[fake_choice])
    )

    agent = OpenAICompatAgent(
        agent_id="local", model_id="llama3.2", family="llama",
        api_key_field=None, base_url="http://localhost:11434/v1",
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        with patch("app.debate.panel.settings") as s:
            s.require.side_effect = AssertionError("require() must not be called")
            result = asyncio.run(agent.respond("sys", "user"))

    assert result == "ok"


class _FlakyAgent:
    """
    Fails with a rate-limit-shaped error a fixed number of times, then
    succeeds -- for testing _call_with_retry / gather_responses' backoff
    behavior without any real network call or real sleep duration.
    """
    agent_id = "flaky"
    model_id = "flaky-model"
    family = "flaky"

    def __init__(self, fail_times: int, error_text: str = "Error code: 429 - rate limited"):
        self.fail_times = fail_times
        self.error_text = error_text
        self.calls = 0

    async def respond(self, system: str, user: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(self.error_text)
        return '{"action": "pass"}'


class _AlwaysFailsNonRateLimitAgent:
    """A genuine (non-rate-limit) failure -- must NOT be retried."""
    agent_id = "broken"
    model_id = "broken-model"
    family = "broken"

    def __init__(self):
        self.calls = 0

    async def respond(self, system: str, user: str) -> str:
        self.calls += 1
        raise ValueError("malformed request: missing required field 'summary'")


def test_gather_responses_retries_rate_limit_errors_and_recovers():
    from app.debate.panel import gather_responses

    async def run():
        with patch("app.debate.panel.asyncio.sleep", new=AsyncMock()):  # no real delay in tests
            agent = _FlakyAgent(fail_times=2)
            results = await gather_responses([agent], system="sys", user="usr")
            assert results["flaky"] == '{"action": "pass"}'
            assert agent.calls == 3, "should have retried exactly twice before succeeding"

    asyncio.run(run())


def test_gather_responses_does_not_retry_non_rate_limit_errors():
    from app.debate.panel import gather_responses

    async def run():
        with patch("app.debate.panel.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            agent = _AlwaysFailsNonRateLimitAgent()
            results = await gather_responses([agent], system="sys", user="usr")
            assert isinstance(results["broken"], ValueError)
            assert agent.calls == 1, "a genuine (non-rate-limit) error must fail immediately, not retry"
            mock_sleep.assert_not_called()

    asyncio.run(run())


def test_gather_responses_gives_up_after_max_retries_on_persistent_rate_limit():
    from app.debate.panel import gather_responses

    async def run():
        with patch("app.debate.panel.asyncio.sleep", new=AsyncMock()):
            agent = _FlakyAgent(fail_times=99)  # never recovers within max_retries
            results = await gather_responses([agent], system="sys", user="usr")
            assert isinstance(results["flaky"], RuntimeError)
            assert agent.calls == 4, "max_retries=3 means 1 initial call + 3 retries = 4 total"

    asyncio.run(run())
