"""
Deterministic, offline test for orchestrator.classify_failure -- proves the
6 distinct failure categories the coordinator's readiness-gate task requires
(model / product / provider / environmental / timeout / task-budget
exhaustion) are genuinely separable, using REAL recorded field values from
this session's own actual trial/calibration runs wherever available (not
invented strings) plus realistic synthetic ones for classes this pass
didn't happen to observe live.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from orchestrator import classify_failure  # noqa: E402


def test_success():
    assert classify_failure({"task_success": True, "error": None}) == "success"


def test_budget_exhaustion_real_step_budget_note():
    # Real string observed this pass, e.g. calibration/raw/T3-A-c53f3144.json:
    # "notes": "step 0 (...): stop_reason=step_budget, tool_calls=25"
    rec = {
        "task_success": False, "error": None, "budget_exceeded": False,
        "notes": "step 0 (rename a function): stop_reason=step_budget, tool_calls=25",
    }
    assert classify_failure(rec) == "budget_exhaustion"


def test_budget_exhaustion_takes_precedence_over_outer_timeout_flag():
    # A trial can carry BOTH signals (hit step budget, which then also
    # exceeds the outer wall-clock ceiling before verification runs) --
    # the more specific, in-band signal must win.
    rec = {
        "task_success": False, "error": None, "budget_exceeded": True,
        "node_notes": ["step 0 (explore repo): stop_reason=step_budget, tool_calls=25"],
    }
    assert classify_failure(rec) == "budget_exhaustion"


def test_timeout_outer_wall_clock_ceiling():
    # asyncio.wait_for's own TimeoutError path in run_one_trial: budget_exceeded=True,
    # error=None, no step_budget note anywhere.
    rec = {"task_success": False, "error": None, "budget_exceeded": True, "notes": ""}
    assert classify_failure(rec) == "timeout"


def test_environmental_failure_real_missing_env_var():
    # Real error string observed this pass (calibration/raw, first attempt
    # before the .env fix): KeyError: 'GENERAL_COMPUTE_API_KEY'.
    rec = {"task_success": False, "budget_exceeded": False,
           "error": "KeyError: 'GENERAL_COMPUTE_API_KEY'"}
    assert classify_failure(rec) == "environmental_failure"


def test_environmental_failure_real_connect_error():
    # Real error text observed this pass when the MCP server failed to
    # bind under resource contention (retrieval_verification.py runs,
    # concurrent with a full backend/tests/ run) -- the connection was
    # never established at all, distinct from a timeout on an established
    # connection (tested separately below).
    rec = {"task_success": False, "budget_exceeded": False,
           "error": "httpx2.ConnectError: All connection attempts failed"}
    assert classify_failure(rec) == "environmental_failure"


def test_provider_failure_real_observed_incident_message():
    # Real message from raw/provider_400_errors/failed_request_*.json,
    # already proven transient by experiments/swebench_pro/agent.py's own
    # is_transient() and its dedicated offline test (test_agent_recovery_
    # offline.py, re-verified this pass). This is what a real, unrecovered
    # provider error looks like once it reaches this layer.
    rec = {"task_success": False, "budget_exceeded": False,
           "error": "Error code: 400 - {'error': {'message': 'Provider request "
                    "failed with status 400', 'type': 'provider_error', "
                    "'code': 'provider_error', 'param': None}}"}
    assert classify_failure(rec) == "provider_failure"


def test_timeout_real_pre_fix_mcp_transport_exceptiongroup():
    # Real error string from the ORIGINAL T7 crash this whole readiness
    # pass traces back to (raw/T7-B_default-c8de47d3.json, pre-fix) --
    # this classifier must still call it "timeout" (a transport-level
    # timeout, the exact bug _MCP_SESSION_HTTP_TIMEOUT_SECONDS fixed), not
    # a generic product_failure, so a regression of that specific fix
    # shows up distinctly in the aggregate counts.
    rec = {"task_success": False, "budget_exceeded": False,
           "error": "ExceptionGroup: unhandled errors in a TaskGroup (2 sub-exceptions)"}
    assert classify_failure(rec) == "timeout"


def test_product_failure_real_stealthlab_exception():
    rec = {"task_success": False, "budget_exceeded": False,
           "error": "ValueError: visibility must be 'public' or 'private', got 'secret'"}
    assert classify_failure(rec) == "product_failure"


def test_model_failure_clean_run_no_error_did_not_solve_task():
    # Real shape from this pass's own 25-step calibration T3/A: a clean
    # run, no error, no budget_exceeded, task genuinely not solved.
    rec = {"task_success": False, "budget_exceeded": False, "error": None,
           "notes": "", "node_notes": []}
    assert classify_failure(rec) == "model_failure"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
