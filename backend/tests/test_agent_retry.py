"""
Tests for the retry policy shared by Agent._complete (agent.py) and
HTNAgent._chat (htn_agent.py): is_transient()/backoff_seconds() in agent.py.

This path had NO coverage, which is exactly why it shipped a bug that
silently discarded whole benchmark instances: openai constructs
APITimeoutError with the message "Request timed out.", and the filter
tested for the substring "timeout" -- which does not appear in "timed
out". A timeout was therefore treated as permanent and raised on the
FIRST attempt, ending an arm at exactly REQUEST_TIMEOUT with zero tokens
spent. summarise() only counts an instance when every arm is valid, so
one such timeout threw away the whole instance, including tokens already
spent by arms that had resolved it.

WHY BOTH AGENTS ARE TESTED HERE, NOT JUST Agent. is_transient/
backoff_seconds used to be two separately-maintained inline copies -- one
in Agent._complete, one in HTNAgent._chat -- and they drifted: the first
copy got fixed for the "timed out" bug and the second did not, so a
Stage 5 sweep died on every htn_memory arm at "tok=0 tools=0" while the
SAME instance's no_memory arm, calling the fixed copy, completed
normally. Testing only Agent is exactly the gap that let that ship.

The exception classes here are STUBS named after openai's, not imports:
agent.py takes an injected client and never imports openai, and the fix
matches on type(exc).__name__ for that reason. Naming the stubs
identically is what makes these tests exercise the real matching rule.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

import agent as agent_mod  # noqa: E402
import htn_agent as htn_agent_mod  # noqa: E402
from agent import MAX_RETRIES, Agent, Usage, backoff_seconds, is_transient  # noqa: E402
from htn_agent import HTNAgent  # noqa: E402


# --- stand-ins for the provider SDK's exception hierarchy -------------------
# Names and messages copied from openai/_exceptions.py, since both the type
# name AND the message text are load-bearing for the matching rule.
class APITimeoutError(Exception):
    def __init__(self):
        super().__init__("Request timed out.")


class APIConnectionError(Exception):
    def __init__(self):
        super().__init__("Connection error.")


class RateLimitError(Exception):
    def __init__(self):
        super().__init__("Error code: 429 - rate limit reached")


class InternalServerError(Exception):
    def __init__(self):
        super().__init__("Error code: 500 - internal server error")


class BadRequestError(Exception):
    """The gateway-wrapped upstream failure: a 400 whose BODY says the
    provider failed. Matched by string, not by type -- the type here is
    genuinely a client error, only the body reveals it is not ours."""

    def __init__(self):
        super().__init__(
            "Error code: 400 - {'error': {'message': 'Provider request "
            "failed with status 400', 'type': 'provider_error'}}")


def _ok_response():
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok", tool_calls=None))],
        usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class ScriptedClient:
    """Raises the scripted exceptions in order, then returns a response."""

    def __init__(self, raises, then_ok=True):
        self.raises = list(raises)
        self.then_ok = then_ok
        self.calls = 0
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises.pop(0)
        if self.then_ok:
            return _ok_response()
        raise AssertionError("script exhausted with then_ok=False")


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff durations instead of actually waiting -- patched in
    BOTH modules, since each has its own `import time` and its own call
    site for the shared backoff_seconds()."""
    slept: list[float] = []
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(htn_agent_mod.time, "sleep", lambda s: slept.append(s))
    return slept


def _agent(client):
    return Agent(client, "m")


def _htn_agent(client):
    return HTNAgent(client, "m")


class TestTimeoutIsRetried:
    """The regression this module exists for."""

    def test_request_timed_out_is_retried_not_raised_immediately(self, no_sleep):
        client = ScriptedClient([APITimeoutError() for _ in range(MAX_RETRIES)],
                                then_ok=False)
        with pytest.raises(APITimeoutError):
            _agent(client)._complete([{"role": "user", "content": "x"}])
        # The whole point: MAX_RETRIES attempts, not one. Before the fix
        # this was 1 -- "timeout" is not a substring of "timed out".
        assert client.calls == MAX_RETRIES

    def test_a_timeout_that_clears_returns_the_response(self, no_sleep):
        client = ScriptedClient([APITimeoutError()])
        resp = _agent(client)._complete([{"role": "user", "content": "x"}])
        assert resp.choices[0].message.content == "ok"
        assert client.calls == 2  # failed once, succeeded on the retry


class TestOtherTransientClasses:
    @pytest.mark.parametrize("exc_factory", [
        APIConnectionError, RateLimitError, InternalServerError, BadRequestError,
    ])
    def test_transient_failures_are_retried(self, exc_factory, no_sleep):
        client = ScriptedClient([exc_factory()])
        resp = _agent(client)._complete([{"role": "user", "content": "x"}])
        assert resp.choices[0].message.content == "ok"
        assert client.calls == 2

    def test_internal_server_error_500_specifically(self, no_sleep):
        """502/503/504 were listed but 500 was not, purely because nobody
        had hit it yet."""
        client = ScriptedClient([InternalServerError() for _ in range(MAX_RETRIES)],
                                then_ok=False)
        with pytest.raises(InternalServerError):
            _agent(client)._complete([{"role": "user", "content": "x"}])
        assert client.calls == MAX_RETRIES


class TestNonTransientStillFailsFast:
    def test_a_real_bug_is_not_retried(self, no_sleep):
        """The fix must not turn every genuine error into a 4x-retried
        hang -- a malformed request should surface on the first attempt."""
        client = ScriptedClient([ValueError("bad request shape")], then_ok=False)
        with pytest.raises(ValueError):
            _agent(client)._complete([{"role": "user", "content": "x"}])
        assert client.calls == 1
        assert no_sleep == []  # never even backed off


class TestBackoffChoice:
    def test_rate_limit_waits_most_of_a_window(self, no_sleep):
        """A 429 is a tokens-per-minute window, not congestion: retrying
        in 4s just burns another attempt inside the same window."""
        client = ScriptedClient([RateLimitError()])
        _agent(client)._complete([{"role": "user", "content": "x"}])
        assert no_sleep == [25.0]

    def test_everything_else_gets_the_short_capped_backoff(self, no_sleep):
        client = ScriptedClient([APITimeoutError()])
        _agent(client)._complete([{"role": "user", "content": "x"}])
        assert no_sleep == [4.0]  # 4 * 2**0, under MAX_BACKOFF

    def test_backoff_is_capped_not_unbounded(self, no_sleep):
        """Uncapped 4,8,16,32... costs 124s per failed call, which at 40
        steps is 83 minutes of sleeping in one episode."""
        client = ScriptedClient([APITimeoutError() for _ in range(MAX_RETRIES)],
                                then_ok=False)
        with pytest.raises(APITimeoutError):
            _agent(client)._complete([{"role": "user", "content": "x"}])
        assert all(s <= agent_mod.MAX_BACKOFF for s in no_sleep)


class TestHTNAgentChatUsesTheSamePolicy:
    """HTNAgent._chat used to carry its own inline copy of this filter,
    and it never got the "timed out" fix -- this is the direct regression
    for the Stage 5 sweep where every htn_memory arm died at
    "tok=0 tools=0" via APITimeoutError while no_memory, calling the
    fixed Agent._complete, ran the same instance to completion."""

    def test_request_timed_out_is_retried_not_raised_immediately(self, no_sleep):
        client = ScriptedClient([APITimeoutError() for _ in range(MAX_RETRIES)],
                                then_ok=False)
        with pytest.raises(APITimeoutError):
            _htn_agent(client)._chat([{"role": "user", "content": "x"}], Usage())
        assert client.calls == MAX_RETRIES

    def test_a_timeout_that_clears_returns_the_response_and_records_usage_once(
            self, no_sleep):
        client = ScriptedClient([APITimeoutError()])
        usage = Usage()
        resp = _htn_agent(client)._chat([{"role": "user", "content": "x"}], usage)
        assert resp.choices[0].message.content == "ok"
        assert client.calls == 2
        # Usage is recorded once, from the call that actually returned --
        # not once per attempt.
        assert usage.calls == 1

    def test_a_real_bug_is_not_retried(self, no_sleep):
        client = ScriptedClient([ValueError("bad request shape")], then_ok=False)
        with pytest.raises(ValueError):
            _htn_agent(client)._chat([{"role": "user", "content": "x"}], Usage())
        assert client.calls == 1
        assert no_sleep == []

    def test_rate_limit_waits_most_of_a_window(self, no_sleep):
        client = ScriptedClient([RateLimitError()])
        _htn_agent(client)._chat([{"role": "user", "content": "x"}], Usage())
        assert no_sleep == [25.0]

    def test_everything_else_gets_the_short_capped_backoff(self, no_sleep):
        client = ScriptedClient([APITimeoutError()])
        _htn_agent(client)._chat([{"role": "user", "content": "x"}], Usage())
        assert no_sleep == [4.0]


class TestBothAgentsAgreeOnClassification:
    """The test that would have caught the drift itself: is_transient must
    give the SAME verdict regardless of which agent is asking, since both
    now call the one function in agent.py rather than keeping their own
    copy. This does not re-drive either agent -- it pins the shared
    function's contract directly, which is what makes drift impossible to
    reintroduce silently."""

    @pytest.mark.parametrize("exc_factory,expected", [
        (APITimeoutError, True),
        (APIConnectionError, True),
        (RateLimitError, True),
        (InternalServerError, True),
        (BadRequestError, True),
        (lambda: ValueError("bad request shape"), False),
    ])
    def test_is_transient_is_one_function_not_two_opinions(self, exc_factory, expected):
        assert is_transient(exc_factory()) is expected

    def test_backoff_seconds_agrees_with_the_rate_limit_special_case(self):
        assert backoff_seconds(RateLimitError(), attempt=0) == 25.0
        assert backoff_seconds(APITimeoutError(), attempt=0) == 4.0
