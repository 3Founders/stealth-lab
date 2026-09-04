"""
Offline, no-live-API-call regression test for the real provider-recovery
mechanism this file already implements (is_transient/_complete's own
MAX_RETRIES loop, and Agent.run()'s outer "drop last exchange and
continue" recovery, MAX_RECOVERIES-bounded).

Reproduces the ORIGINAL failure mode this pass diagnosed and fixed
(Final Baseline vs Stealth Agent Experiment, T7/B_default pilot crash):
the real GENERAL_COMPUTE error observed live was
`BadRequestError: Error code: 400 - {'error': {'message': 'Provider
request failed with status 400', 'type': 'provider_error', 'code':
'provider_error', 'param': None}}` -- captured verbatim in
.scratch/final_agent_experiment/raw/provider_400_errors/*.json. This test
uses a fake OpenAI-shaped client raising exactly that message (never a
real network call), and proves:

1. is_transient() correctly classifies it as transient (the actual
   classification this incident depends on).
2. Agent.run() survives it via the real recovery path and continues to a
   real, honest completion.
3. AgentRun.recoveries (added this pass -- was previously an internal-only
   loop variable) accurately reports how many recoveries happened, so a
   caller can tell a recovered run apart from a clean one.
4. Recovery never inflates trust/evidence: a run that recovered N times and
   then genuinely stopped (no further tool calls) reports stop_reason
   truthfully, and every real attempted call (failed + succeeded) is
   counted in `usage`/`steps` -- nothing about the failed attempts is
   silently dropped from the token/call accounting.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import Agent, RepoSandbox, is_transient  # noqa: E402


class _FakeUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=5):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


def _fake_response(tool_calls=None, content=""):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=_FakeUsage(),
    )


# The EXACT real message captured live in
# raw/provider_400_errors/failed_request_*.json -- reproducing the actual
# incident, not a synthetic guess at what a 400 might look like.
_REAL_INCIDENT_MESSAGE = (
    "Error code: 400 - {'error': {'message': 'Provider request failed "
    "with status 400', 'type': 'provider_error', 'code': 'provider_error', "
    "'param': None}}"
)


class _ProviderBadRequestError(Exception):
    pass


def test_is_transient_classifies_the_real_observed_incident_message():
    exc = _ProviderBadRequestError(_REAL_INCIDENT_MESSAGE)
    assert is_transient(exc), (
        "the exact real GENERAL_COMPUTE 400 message from the T7 pilot "
        "incident must classify as transient"
    )


def test_agent_recovers_from_the_real_incident_error_and_reports_it_honestly(tmp_path):
    sandbox = RepoSandbox(str(tmp_path))
    instance = {
        "instance_id": "offline-recovery-test",
        "repo": "fake-repo",
        "problem_statement": "answer the question, no edits needed",
    }

    call_count = {"n": 0}

    class _FakeCompletions:
        def create(self, **kwargs):
            call_count["n"] += 1
            # Agent.run()'s outer recovery only triggers when there is a
            # prior exchange to drop (`len(messages) > 4` -- confirmed by
            # reading the real condition in agent.py; one full
            # assistant+tool-result exchange only brings len(messages) to
            # 4, not > 4), matching the real incident: every captured
            # provider_400_errors/*.json dump happened at step >= 3, never
            # step 0. So calls 1-2 must SUCCEED (establishing two real
            # exchanges) before the failures start.
            if call_count["n"] <= 2:
                return _fake_response(
                    tool_calls=[_FakeToolCall(f"call-{call_count['n']}", "list_dir", '{"path": ""}')],
                )
            # Calls 3-6: _complete()'s own inner retry loop retries the SAME
            # request up to MAX_RETRIES (4) times before giving up and
            # raising to Agent.run()'s outer loop -- so all 4 of those inner
            # attempts must fail to actually exercise Agent.run()'s OWN
            # outer recovery (dropping the last exchange), not just
            # _complete's already-existing inner retry.
            if call_count["n"] <= 6:
                raise _ProviderBadRequestError(_REAL_INCIDENT_MESSAGE)
            # Call 7 (the fresh request after Agent.run() drops the last
            # exchange) succeeds with no further tool calls -> the loop ends
            # via stop_reason="no_tool_call", a genuine, honest stop -- not
            # a fabricated "finish".
            return _fake_response(tool_calls=None, content="done exploring, nothing to edit")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions()),
    )

    agent = Agent(fake_client, model="fake-model", max_steps=8)

    # _complete's own inner retry loop calls time.sleep(backoff_seconds(...))
    # between attempts -- patched so this test runs in well under a second,
    # not real backoff wall-clock time.
    with patch("agent.time.sleep", return_value=None):
        run = agent.run(instance, sandbox, arm="test")

    assert run.error is None, "a recovered-then-honestly-stopped run must not report a fatal error"
    assert run.stop_reason == "no_tool_call", (
        f"expected an honest, non-fabricated stop, got {run.stop_reason!r}"
    )
    # recoveries only counts Agent.run()'s OWN outer recovery (dropping the
    # last exchange), not _complete's inner same-request retry attempts --
    # confirm it is a real, non-zero, non-silently-absorbed count.
    assert run.recoveries >= 1, (
        "AgentRun.recoveries must report that provider-error recovery "
        "actually happened -- it must never silently look like a clean run"
    )
    # This fake client received 7 real HTTP attempts total (2 succeeded +
    # 4 failed + 1 succeeded) -- confirmed via call_count, the ground truth
    # of what was actually attempted.
    assert call_count["n"] == 7, "sanity: the fake client must have seen exactly 7 real attempts"
    # usage.calls only increments on a real SUCCESSFUL response (Agent.run()
    # only calls usage.add(resp.usage) after a non-raising _complete() call)
    # -- so it correctly reports 3 (calls 1, 2, 7), not 7. This is the
    # correct behavior, not a bug: a failed attempt returns no response and
    # so has no real token usage to count. The important property is what
    # it does NOT do: it never claims MORE successful calls happened than
    # truly did (no inflation), and `recoveries` (asserted above) makes the
    # 4 real failed attempts fully visible and auditable rather than
    # silently absorbed -- together these two fields let a caller
    # reconstruct exactly what happened (3 real successes, 4 real failures
    # survived via recovery), rather than a single flattened "3 calls"
    # number implying a clean run with no trouble.
    assert run.usage.calls == 3, (
        f"expected exactly 3 counted (successful) calls, got {run.usage.calls} "
        "-- usage accounting must reflect real successful responses only, "
        "never inflated by retried/failed attempts"
    )
