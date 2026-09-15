"""
DB-free coverage for app.execution.goal_verification -- the real Goal-
level verification contract (Prompt 2 Sec 9). `deterministic_check`
tests run a REAL sandboxed subprocess (same convention
test_implementation_providers_offline.py already establishes for
DeterministicProvider -- no DB, no network, just a real local Python
subprocess), proving the wrapper script actually executes and reports
real exit codes, not a mocked stand-in.
"""
from __future__ import annotations

import asyncio

import app.execution.goal_verification as gv
from app.execution.graph_executor import NodeResult


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# no contract / unsupported method -- never auto-passes
# ---------------------------------------------------------------------


def test_empty_contract_is_honestly_unverified():
    outcome = _run(gv.run_goal_verification({}))
    assert outcome.state == "unverified"
    assert outcome.method == ""


def test_unsupported_method_is_honestly_unverified_never_auto_passed():
    outcome = _run(gv.run_goal_verification({"method": "metric_threshold", "metric": "p99_latency_ms"}))
    assert outcome.state == "unverified"
    assert "not automatically checkable" in outcome.detail


def test_human_review_is_never_auto_passed():
    outcome = _run(gv.run_goal_verification({"method": "human_review"}))
    assert outcome.state == "needs_human_review"


# ---------------------------------------------------------------------
# deterministic_check -- REAL sandboxed subprocess
# ---------------------------------------------------------------------


def test_deterministic_check_missing_command_is_unverified():
    outcome = _run(gv.run_goal_verification({"method": "deterministic_check"}))
    assert outcome.state == "unverified"
    assert "missing a real 'command'" in outcome.detail


def test_deterministic_check_real_success_reaches_verified():
    outcome = _run(gv.run_goal_verification({"method": "deterministic_check", "command": "exit 0"}))
    assert outcome.state == "verified"
    assert outcome.method == "deterministic_check"


def test_deterministic_check_real_failure_reaches_failed_verification():
    outcome = _run(gv.run_goal_verification({"method": "deterministic_check", "command": "exit 1"}))
    assert outcome.state == "failed_verification"
    assert "exited 1" in outcome.detail


class _FakeTimeoutExecutor:
    async def run(self, code, input_files, timeout_seconds, network_access=False):
        from app.services.sandbox_executor import ExecutionResult
        return ExecutionResult(exit_code=-1, stdout="", stderr="", timed_out=True)


def test_deterministic_check_timeout_is_failed_verification():
    outcome = _run(gv.run_goal_verification(
        {"method": "deterministic_check", "command": "sleep 999"}, executor=_FakeTimeoutExecutor(),
    ))
    assert outcome.state == "failed_verification"
    assert "timed out" in outcome.detail


# ---------------------------------------------------------------------
# artifact_inspection -- checks the implementation's own real result
# ---------------------------------------------------------------------


def test_artifact_inspection_missing_expected_files_key_is_unverified():
    outcome = _run(gv.run_goal_verification({"method": "artifact_inspection"}))
    assert outcome.state == "unverified"


def test_artifact_inspection_passes_when_all_expected_files_present():
    node_result = NodeResult(status="success", data={"output_files": {"dist/bundle.js": b"...", "other.txt": b"x"}})
    outcome = _run(gv.run_goal_verification(
        {"method": "artifact_inspection", "expected_files": ["dist/bundle.js"]}, node_result,
    ))
    assert outcome.state == "checked"


def test_artifact_inspection_fails_when_expected_file_missing():
    node_result = NodeResult(status="success", data={"output_files": {}})
    outcome = _run(gv.run_goal_verification(
        {"method": "artifact_inspection", "expected_files": ["dist/bundle.js"]}, node_result,
    ))
    assert outcome.state == "failed_verification"
    assert "dist/bundle.js" in outcome.detail


def test_artifact_inspection_with_no_node_result_is_failed_not_fabricated_pass():
    outcome = _run(gv.run_goal_verification({"method": "artifact_inspection", "expected_files": ["a.txt"]}, None))
    assert outcome.state == "failed_verification"
