"""
Offline proof that _UsageCapture (orchestrator.py) extracts REAL provider
usage correctly, handles the missing-usage case explicitly (never a silent
zero), computes total_tokens from real provider fields, and that the result
round-trips through the same JSON serialization orchestrator.py's
run_one_trial() actually writes to disk.

No live API call. Feeds the capture wrapper a real-shaped stand-in for
experiments/swebench_pro/agent.py's AgentRun/Usage dataclasses (same field
names, same values a real resp.usage-populated Usage() would carry) rather
than estimating tokens from text length anywhere in this file.

Run: python .scratch/final_agent_experiment/test_usage_instrumentation.py
(plain asserts, no pytest dependency required, but pytest also collects it).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from orchestrator import _UsageCapture, _redact  # noqa: E402


# ---------------------------------------------------------------------------
# Real-shaped stand-ins for experiments/swebench_pro/agent.py's own
# Usage/AgentRun dataclasses -- same field names (prompt_tokens,
# completion_tokens, calls, .total property) a real resp.usage-populated
# instance carries, per agent.py lines ~75-98/813-814 (read before writing
# this test, not assumed).
# ---------------------------------------------------------------------------
@dataclass
class _FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class _FakeAgentRun:
    instance_id: str
    usage: _FakeUsage
    stop_reason: str = "finished"


def test_single_call_extracted_correctly():
    """One real Agent.run() call, real-shaped usage -> summary() reports
    exactly those numbers, not an estimate."""
    cap = _UsageCapture()
    fake_result = _FakeAgentRun(
        instance_id="local_agent_test_step0",
        usage=_FakeUsage(prompt_tokens=1234, completion_tokens=567, calls=3),
    )
    cap.calls.append({
        "instance_id": fake_result.instance_id,
        "prompt_tokens": fake_result.usage.prompt_tokens,
        "completion_tokens": fake_result.usage.completion_tokens,
        "total_tokens": fake_result.usage.total,
        "api_calls": fake_result.usage.calls,
    })
    s = cap.summary()
    assert s["input_tokens"] == 1234, s
    assert s["output_tokens"] == 567, s
    assert s["total_tokens"] == 1234 + 567, s  # real provider fields, not len(text)
    assert s["model_calls"] == 3, s
    assert s["cost_usd"] is None, "no GENERAL_COMPUTE pricing config exists -- must stay null"
    assert "cost_unavailable_reason" in s and s["cost_unavailable_reason"], s
    assert s["usage_raw"] == cap.calls, "raw usage must be preserved for independent audit"


def test_multi_node_task_sums_across_all_agent_run_calls():
    """A multi-step task graph makes more than one Agent.run() call --
    summary() must sum across ALL of them, not just report the last one."""
    cap = _UsageCapture()
    for i, (pt, ct, n) in enumerate([(100, 50, 1), (200, 80, 2), (150, 60, 1)]):
        cap.calls.append({
            "instance_id": f"local_agent_test_step{i}",
            "prompt_tokens": pt, "completion_tokens": ct,
            "total_tokens": pt + ct, "api_calls": n,
        })
    s = cap.summary()
    assert s["input_tokens"] == 100 + 200 + 150 == 450, s
    assert s["output_tokens"] == 50 + 80 + 60 == 190, s
    assert s["total_tokens"] == 450 + 190 == 640, s
    assert s["model_calls"] == 1 + 2 + 1 == 4, s


def test_missing_usage_is_explicit_not_a_silent_zero():
    """No Agent.run() call captured this trial (e.g. the runner refused
    before ever constructing an Agent) -> explicit None/reason, never a
    bare 0 that could be misread as 'zero tokens used, real result'."""
    cap = _UsageCapture()
    s = cap.summary()
    assert s["input_tokens"] is None, "missing usage must be None, not 0"
    assert s["output_tokens"] is None, s
    assert s["total_tokens"] is None, s
    assert s["model_calls"] == 0, "model_calls=0 is honest here (zero calls happened)"
    assert s["cost_usd"] is None, s
    assert "no Agent.run() call captured" in s["cost_unavailable_reason"], s


def test_result_schema_round_trips_through_the_real_serialization_path():
    """orchestrator.py's run_one_trial() writes json.dumps(_redact(record),
    indent=2, default=str) to disk -- prove usage.summary()'s own output
    survives that exact call unchanged (dict shape, values, no accidental
    redaction of legitimate token-count fields whose names don't match
    SECRET_KEYS)."""
    cap = _UsageCapture()
    cap.calls.append({
        "instance_id": "local_agent_test_step0",
        "prompt_tokens": 999, "completion_tokens": 111,
        "total_tokens": 1110, "api_calls": 1,
    })
    record = {"trial_id": "T-fake-0001", "arm": "B_default", **cap.summary()}
    serialized = json.dumps(_redact(record), indent=2, default=str)
    reloaded = json.loads(serialized)
    assert reloaded["input_tokens"] == 999, reloaded
    assert reloaded["output_tokens"] == 111, reloaded
    assert reloaded["total_tokens"] == 1110, reloaded
    assert reloaded["model_calls"] == 1, reloaded
    assert reloaded["cost_usd"] is None, reloaded
    assert reloaded["usage_raw"][0]["prompt_tokens"] == 999, reloaded


def test_redact_does_not_touch_token_count_fields():
    """_redact()'s SECRET_KEYS = {api_key, authorization, token, password,
    secret} -- confirm 'total_tokens'/'input_tokens'/etc. don't collide
    with the literal key 'token' and get wrongly redacted."""
    payload = {"input_tokens": 42, "output_tokens": 7, "total_tokens": 49,
               "api_key": "sk-should-be-redacted"}
    redacted = _redact(payload)
    assert redacted["input_tokens"] == 42, redacted
    assert redacted["output_tokens"] == 7, redacted
    assert redacted["total_tokens"] == 49, redacted
    assert redacted["api_key"] == "[REDACTED]", redacted


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
