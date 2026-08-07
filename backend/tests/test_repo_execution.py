"""
Tests for sandboxed repo execution.

Covers everything that can be checked without a Docker daemon: the
security-relevant flags on the container command, patch-application
ordering, log parsing, and the verdict logic. The parts that genuinely
need Docker live in integration_check_v2_repo_execution.py, matching this
project's existing split between unit tests and live checks.
"""
from __future__ import annotations

from unittest.mock import patch as mock_patch

import pytest

from app.services.repo_execution import (
    DockerUnavailable,
    TestOutcome,
    _as_list,
    _build_script,
    _default_test_command,
    classify,
    decide,
    docker_command,
    evaluate_patch,
    parse_pytest_log,
)


# --- The security invariant. If one test in this file matters, it's this. ---

def test_container_has_no_network():
    cmd = docker_command("img", "echo hi")
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"


def test_container_is_resource_bounded_and_ephemeral():
    cmd = docker_command("img", "echo hi", memory="2g", cpus="1")
    assert "--rm" in cmd                      # no state survives between instances
    assert cmd[cmd.index("--memory") + 1] == "2g"
    assert cmd[cmd.index("--cpus") + 1] == "1"
    assert "--pids-limit" in cmd


def test_script_is_not_interpolated_into_the_command_line():
    """
    Patch text contains quotes, newlines and $-signs. If the script were
    baked into argv, a patch could break out of our own quoting. It goes
    over stdin instead -- confirm the command ends at `bash -s` and carries
    no script content.
    """
    nasty = "'; rm -rf /; echo '"
    cmd = docker_command("img", nasty)
    assert cmd[-2:] == ["bash", "-s"]
    assert all(nasty not in part for part in cmd)


def test_fails_closed_when_docker_is_missing():
    """Never runs a patch unsandboxed as a fallback."""
    instance = {"instance_id": "a__b-1", "FAIL_TO_PASS": '["t1"]', "PASS_TO_PASS": "[]"}
    with mock_patch("app.services.repo_execution.docker_available", return_value=False):
        result = evaluate_patch(instance, "diff --git a b")
    assert result.isolation_failed is True
    assert result.resolved is False


# --- Patch application order ---

def test_test_patch_is_applied_before_the_candidate_patch():
    """
    FAIL_TO_PASS tests only exist once test_patch lands. Applying the
    candidate first would leave them uncollected and parse as `missing`,
    which reads as broken plumbing rather than the real result.
    """
    script = _build_script("CANDIDATE_DIFF", "TEST_DIFF", "pytest")
    assert script.index("TEST_DIFF") < script.index("CANDIDATE_DIFF")
    assert script.index("CANDIDATE_DIFF") < script.index("pytest")


def test_failed_patch_application_exits_with_the_reserved_code():
    script = _build_script("CANDIDATE", "TESTS", "pytest")
    assert script.count("exit 97") == 2  # one per applied patch


# --- Log parsing ---

def test_parses_both_pytest_orderings():
    log = (
        "tests/test_a.py::test_one PASSED\n"
        "FAILED tests/test_b.py::test_two\n"
    )
    parsed = parse_pytest_log(log)
    assert parsed["tests/test_a.py::test_one"] == "PASSED"
    assert parsed["tests/test_b.py::test_two"] == "FAILED"


def test_a_test_name_containing_the_word_passed_is_not_a_false_positive():
    """Patterns are line-anchored so `test_passed_flag` can't fake a result."""
    parsed = parse_pytest_log("  incidental mention of PASSED tests/x.py::test_thing\n")
    assert "tests/x.py::test_thing" not in parsed


def test_error_is_kept_distinct_from_passed():
    parsed = parse_pytest_log("ERROR tests/test_c.py::test_three\n")
    assert parsed["tests/test_c.py::test_three"] == "ERROR"


# --- Classification ---

def test_a_test_that_never_ran_is_missing_not_failed():
    """
    The distinction that protects the ground truth: a test that didn't run
    (import error, renamed upstream) is a harness problem, not evidence the
    patch was wrong.
    """
    outcome = classify(["t1", "t2"], {"t1": "PASSED"})
    assert outcome.passed == {"t1"}
    assert outcome.missing == {"t2"}
    assert outcome.failed == set()


# --- The verdict ---

def test_resolved_requires_f2p_passing_and_p2p_intact():
    outcome, resolved = decide(
        TestOutcome(passed={"t1"}), TestOutcome(passed={"t2"})
    )
    assert (outcome, resolved) == ("success", True)


def test_a_regression_is_needs_rework_not_plain_failure():
    """
    Fix works, something else broke. Genuinely different from not fixing
    it, and the debate mechanism should see that difference.
    """
    outcome, resolved = decide(
        TestOutcome(passed={"t1"}), TestOutcome(failed={"t2"})
    )
    assert outcome == "needs_rework"
    assert resolved is False


def test_unfixed_issue_is_failure():
    outcome, resolved = decide(
        TestOutcome(failed={"t1"}), TestOutcome(passed={"t2"})
    )
    assert (outcome, resolved) == ("failure", False)


def test_missing_tests_never_count_as_success():
    """A manufactured pass here would corrupt every downstream metric."""
    outcome, resolved = decide(
        TestOutcome(missing={"t1"}), TestOutcome(passed={"t2"})
    )
    assert resolved is False


# --- Dataset field handling ---

def test_accepts_both_json_encoded_and_real_lists():
    assert _as_list('["a", "b"]') == ["a", "b"]
    assert _as_list(["a", "b"]) == ["a", "b"]
    assert _as_list(None) == []


def test_default_test_command_quotes_test_names():
    cmd = _default_test_command(["tests/t.py::test_x[weird arg]"])
    assert "pytest" in cmd
    assert "'tests/t.py::test_x[weird arg]'" in cmd
