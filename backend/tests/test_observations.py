"""
Tests for the pure-function extractors in observations.py. The
deterministic extractor needs no DB/network at all. The model extractor
uses a scripted fake client (same pattern as this project's other
LLM-calling tools) since this sandbox has no real network path to
General Compute.
"""
import asyncio
import json

from app.services.observations import (
    extract_deterministic_observations,
    extract_model_observation,
    _looks_like_test_command,
)


class TestDeterministicExtractor:
    def test_edit_produces_file_touched_observation(self):
        event = {"tool_name": "Edit", "tool_input": {"file_path": "src/auth.py"}}
        obs = extract_deterministic_observations(event)
        assert len(obs) == 1
        assert obs[0]["observation_type"] == "file_touched"
        assert "src/auth.py" in obs[0]["label"]
        assert obs[0]["properties"]["file_path"] == "src/auth.py"

    def test_write_and_multiedit_also_produce_file_touched(self):
        for tool in ("Write", "MultiEdit"):
            event = {"tool_name": tool, "tool_input": {"file_path": "x.py"}}
            obs = extract_deterministic_observations(event)
            assert obs[0]["observation_type"] == "file_touched"

    def test_bash_git_commit_produces_commit_made(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix bug'"}}
        obs = extract_deterministic_observations(event)
        assert len(obs) == 1
        assert obs[0]["observation_type"] == "commit_made"

    def test_bash_pytest_produces_test_run(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "pytest tests/ -v"}}
        obs = extract_deterministic_observations(event)
        assert obs[0]["observation_type"] == "test_run"

    def test_bash_npm_test_produces_test_run(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "npm test"}}
        obs = extract_deterministic_observations(event)
        assert obs[0]["observation_type"] == "test_run"

    def test_bash_other_command_produces_command_executed(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        obs = extract_deterministic_observations(event)
        assert obs[0]["observation_type"] == "command_executed"

    def test_read_tool_produces_no_observation(self):
        """A real, important negative case: not every tool call should
        produce noise. Read has no side effect worth recording here."""
        event = {"tool_name": "Read", "tool_input": {"file_path": "x.py"}}
        obs = extract_deterministic_observations(event)
        assert obs == []

    def test_bash_with_no_command_produces_nothing(self):
        event = {"tool_name": "Bash", "tool_input": {}}
        obs = extract_deterministic_observations(event)
        assert obs == []

    def test_tool_input_as_json_string_is_parsed(self):
        """Real, live shape check: asyncpg returns JSONB columns as
        Python dicts already, but tool_input might arrive as a raw JSON
        string depending on the call site -- confirm both work."""
        event = {"tool_name": "Edit", "tool_input": json.dumps({"file_path": "y.py"})}
        obs = extract_deterministic_observations(event)
        assert obs[0]["properties"]["file_path"] == "y.py"

    def test_test_command_marker_matching_is_case_insensitive(self):
        assert _looks_like_test_command("PYTEST tests/")
        assert _looks_like_test_command("run Jest now")
        assert not _looks_like_test_command("cat test_file.txt")  # real, honest limit:
        # "test" appearing in an unrelated command doesn't false-positive
        # here since the real markers are full tool-invocation tokens
        # ("pytest", "jest"), not the bare word "test"


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _ScriptedClient:
    """Stands in for the real LLM client -- returns a fixed response,
    same honest-stub pattern as this project's other tests for
    network-blocked LLM calls."""
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_call = None
        self.chat = type("C", (), {"completions": type("Comp", (), {"create": self._create})()})()

    def _create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self._response_text)


class TestModelExtractor:
    def test_real_label_is_parsed_and_returned(self):
        async def _run():
            client = _ScriptedClient("Authentication logic was modified")
            event = {"tool_name": "Edit", "tool_input": {"file_path": "auth.py"},
                      "tool_output": {"success": True}}
            result = await extract_model_observation(event, client)
            assert result is not None
            assert result["label"] == "Authentication logic was modified"
            assert result["observation_type"] == "semantic_label"
            assert result["model_id"] == "gemma-4-31B-it"
            assert result["prompt_hash"] is not None
            assert result["decoding_params_hash"] is not None
        asyncio.run(_run())

    def test_none_response_is_honored_not_stored_as_a_label(self):
        """The real, deliberate escape hatch: not every event deserves a
        semantic label, and the contract for saying so must actually work."""
        async def _run():
            client = _ScriptedClient("NONE")
            event = {"tool_name": "Glob", "tool_input": {}}
            result = await extract_model_observation(event, client)
            assert result is None
        asyncio.run(_run())

    def test_empty_response_is_also_treated_as_none(self):
        async def _run():
            client = _ScriptedClient("")
            event = {"tool_name": "Glob", "tool_input": {}}
            result = await extract_model_observation(event, client)
            assert result is None
        asyncio.run(_run())

    def test_prompt_hash_is_stable_across_calls(self):
        """Real check on the versioning design: the same system prompt
        must hash to the same value every time, or "which observations
        came from prompt X" (the whole point of stamping this) breaks."""
        async def _run():
            client1 = _ScriptedClient("label one")
            client2 = _ScriptedClient("label two")
            event = {"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}
            r1 = await extract_model_observation(event, client1)
            r2 = await extract_model_observation(event, client2)
            assert r1["prompt_hash"] == r2["prompt_hash"]
        asyncio.run(_run())

    def test_tool_output_as_json_string_is_parsed(self):
        async def _run():
            client = _ScriptedClient("something happened")
            event = {"tool_name": "Bash", "tool_input": {"command": "ls"},
                      "tool_output": json.dumps({"stdout": "file.txt"})}
            result = await extract_model_observation(event, client)
            assert result is not None  # did not crash on the string-shaped output
        asyncio.run(_run())
