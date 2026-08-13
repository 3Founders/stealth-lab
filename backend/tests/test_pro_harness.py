"""
Unit tests for the SWE-bench Pro harness's pure logic.

Scoped deliberately to the parts that decide a verdict without Docker: the
entryscript's ordering, the grading rule, and the dataset's list encoding.
Those are where a silent bug produces a plausible-looking accuracy number
rather than an error, which is the failure mode worth testing against.

Whether containers actually start is not testable here and is covered by
smoke_test.py against a real daemon.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(
    # experiments/ lives at the REPO ROOT, not under backend/ -- two levels
    # up from backend/tests/, not one. It moved, and the old single-".."
    # path silently resolved to a directory that no longer exists, which
    # surfaced as a collection error rather than a skip.
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

import pro_harness  # noqa: E402
from pro_harness import (  # noqa: E402
    DOCKER_PULL_MAX_RETRIES, HarnessError, _grade, build_entryscript, pylist,
    pull_image, shell_quote_env, strip_binary_hunks,
)


SAMPLE = {
    "instance_id": "instance_ansible__ansible-abc-vdef",
    "base_commit": "deadbeef",
    "before_repo_set_cmd": (
        "git reset --hard deadbeef\ngit clean -fd\n"
        "git checkout solution -- test/units/test_x.py"
    ),
    "selected_test_files_to_run": '["test/units/test_x.py"]',
    "fail_to_pass": "['test/units/test_x.py::test_new']",
    "pass_to_pass": "['test/units/test_x.py::test_old']",
}


class TestListParsing:
    def test_python_repr_with_single_quotes(self):
        # The published dataset stores these as Python reprs, not JSON.
        # Assuming JSON here would silently yield an empty expectation set,
        # and an empty F2P grades as trivially resolved.
        assert pylist("['a', 'b']") == ["a", "b"]

    def test_json_encoding_also_accepted(self):
        assert pylist('["a", "b"]') == ["a", "b"]

    def test_embedded_apostrophe(self):
        parsed = pylist("[\"key doesn't exist\"]")
        assert parsed == ["key doesn't exist"]

    @pytest.mark.parametrize("value", [None, "", "[]", "not a list at all"])
    def test_degenerate_inputs_give_empty(self, value):
        assert pylist(value) == []


class TestEntryscript:
    def test_patch_is_applied_before_tests_are_checked_out(self):
        """The ordering that stops an agent passing by editing the tests."""
        script = build_entryscript(SAMPLE, [])
        apply_at = script.index("git apply -v /workspace/patch.diff")
        tests_at = script.index("git checkout solution -- test/units/test_x.py")
        assert apply_at < tests_at

    def test_only_the_last_line_of_before_repo_set_cmd_is_used(self):
        # The earlier lines re-do the reset the entryscript already did;
        # replaying them would undo the candidate patch.
        script = build_entryscript(SAMPLE, [])
        assert "git clean -fd" not in script
        assert "git checkout solution -- test/units/test_x.py" in script

    def test_apply_outcome_is_recorded(self):
        script = build_entryscript(SAMPLE, [])
        assert "echo applied > /workspace/apply_status" in script
        assert "echo failed > /workspace/apply_status" in script
        assert "echo empty > /workspace/apply_status" in script

    def test_env_values_with_spaces_are_quoted(self):
        # PYTEST_ADDOPTS in these images has spaces; unquoted, the shell
        # exports only its first word and the test flags vanish.
        quoted = shell_quote_env("PYTEST_ADDOPTS=--tb=short -v --reruns=3")
        assert quoted == "PYTEST_ADDOPTS='--tb=short -v --reruns=3'"
        assert "export PYTEST_ADDOPTS='--tb=short -v'" in build_entryscript(
            SAMPLE, ["PYTEST_ADDOPTS=--tb=short -v"]
        )


class TestGrading:
    @staticmethod
    def _workspace(tmp_path, tests, apply_status="applied"):
        (tmp_path / "output.json").write_text(
            json.dumps({"tests": tests}), encoding="utf-8"
        )
        (tmp_path / "apply_status").write_text(apply_status, encoding="utf-8")
        return str(tmp_path)

    def test_resolved_needs_f2p_pass_and_p2p_intact(self, tmp_path):
        ws = self._workspace(tmp_path, [
            {"name": "test/units/test_x.py::test_new", "status": "PASSED"},
            {"name": "test/units/test_x.py::test_old", "status": "PASSED"},
        ])
        assert _grade(SAMPLE, ws, 0, False).resolved

    def test_broken_p2p_is_not_resolved(self, tmp_path):
        ws = self._workspace(tmp_path, [
            {"name": "test/units/test_x.py::test_new", "status": "PASSED"},
            {"name": "test/units/test_x.py::test_old", "status": "FAILED"},
        ])
        res = _grade(SAMPLE, ws, 0, False)
        assert not res.resolved
        assert res.status == "p2p_broke"

    def test_missing_test_counts_against_resolution(self, tmp_path):
        """A test that never ran is not evidence the patch worked. Treating
        absence as a pass is the one bug that manufactures successes."""
        ws = self._workspace(tmp_path, [
            {"name": "test/units/test_x.py::test_old", "status": "PASSED"},
        ])
        res = _grade(SAMPLE, ws, 0, False)
        assert not res.resolved
        assert res.f2p_missing == ["test/units/test_x.py::test_new"]

    def test_failed_apply_is_distinguishable_from_a_wrong_fix(self, tmp_path):
        ws = self._workspace(tmp_path, [], apply_status="failed")
        assert _grade(SAMPLE, ws, 1, False).status == "patch_failed"

    def test_timeout_without_output_is_not_a_test_failure(self, tmp_path):
        (tmp_path / "apply_status").write_text("applied", encoding="utf-8")
        res = _grade(SAMPLE, str(tmp_path), -1, True)
        assert res.status == "timeout"
        assert not res.resolved


class TestBinaryHunks:
    def test_binary_section_dropped_text_section_kept(self):
        patch = (
            "diff --git a/img.png b/img.png\nGIT binary patch\nliteral 1\n\n"
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
        )
        out = strip_binary_hunks(patch)
        assert "img.png" not in out
        assert "a.py" in out


class _Proc:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


class TestPullImageRetries:
    """pull_image had ZERO retry until this: one docker pull, any failure
    raised immediately. Direct regression for a Stage 5 sweep where 4 of 5
    instances died on "dial tcp: lookup registry-1.docker.io: no such
    host" -- a DNS blip that had already cleared minutes later -- while
    the 5th (an LLM APIConnectionError) survived precisely because THAT
    path already retries. This class is what makes docker pulls match
    that same standard."""

    def _no_sleep(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(pro_harness.time, "sleep", lambda s: slept.append(s))
        return slept

    def _never_present(self, monkeypatch):
        monkeypatch.setattr(pro_harness, "image_present", lambda image: False)

    def test_dns_failure_is_retried_then_succeeds(self, monkeypatch):
        self._never_present(monkeypatch)
        slept = self._no_sleep(monkeypatch)
        calls = []
        results = [
            _Proc(1, "Error response from daemon: ... dial tcp: lookup "
                     "registry-1.docker.io: no such host"),
            _Proc(0, ""),
        ]

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return results.pop(0)

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        pull_image("some/image:tag")  # must not raise
        assert len(calls) == 2
        assert len(slept) == 1

    def test_outage_that_outlasts_the_window_still_raises(self, monkeypatch):
        self._never_present(monkeypatch)
        self._no_sleep(monkeypatch)
        stderr = "dial tcp: lookup registry-1.docker.io: no such host"
        calls = []

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return _Proc(1, stderr)

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        with pytest.raises(HarnessError, match="no such host"):
            pull_image("some/image:tag")
        assert len(calls) == DOCKER_PULL_MAX_RETRIES

    def test_missing_image_fails_on_the_first_attempt(self, monkeypatch):
        """A 404 is not a network problem. Retrying it just burns the
        whole backoff window before failing anyway."""
        self._never_present(monkeypatch)
        slept = self._no_sleep(monkeypatch)
        calls = []

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return _Proc(1, "manifest unknown: manifest tag does not exist")

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        with pytest.raises(HarnessError, match="manifest unknown"):
            pull_image("some/image:tag")
        assert len(calls) == 1
        assert slept == []

    def test_auth_failure_also_fails_fast(self, monkeypatch):
        self._never_present(monkeypatch)
        slept = self._no_sleep(monkeypatch)
        calls = []

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return _Proc(1, "unauthorized: authentication required")

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        with pytest.raises(HarnessError):
            pull_image("some/image:tag")
        assert len(calls) == 1
        assert slept == []

    def test_image_already_present_never_calls_run(self, monkeypatch):
        monkeypatch.setattr(pro_harness, "image_present", lambda image: True)

        def fail(cmd, timeout=300):
            raise AssertionError("_run should not be called")

        monkeypatch.setattr(pro_harness, "_run", fail)
        pull_image("some/image:tag")  # must not raise, must not call _run

    @pytest.mark.parametrize("stderr", [
        "connection refused",
        "connection reset by peer",
        "i/o timeout",
        "net/http: TLS handshake timeout",
        "network is unreachable",
        "temporary failure in name resolution",
    ])
    def test_every_listed_transient_substring_is_retried(self, stderr, monkeypatch):
        """Only "no such host" was exercised above; the classifier lists
        9 substrings total -- this is what actually proves the other 6
        aren't dead entries that look covered but never get hit."""
        self._never_present(monkeypatch)
        self._no_sleep(monkeypatch)
        results = [_Proc(1, stderr), _Proc(0, "")]
        calls = []

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return results.pop(0)

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        pull_image("some/image:tag")  # must not raise
        assert len(calls) == 2

    def test_an_unrecognized_error_is_fail_fast_by_default(self, monkeypatch):
        """The default must be fail-fast, not accidentally permissive --
        manifest unknown/unauthorized above could coincidentally be the
        ONLY two strings treated as non-transient if the classifier were
        an allowlist-shaped bug rather than the intended denylist-by-
        omission."""
        self._never_present(monkeypatch)
        slept = self._no_sleep(monkeypatch)
        calls = []

        def fake_run(cmd, timeout=300):
            calls.append(cmd)
            return _Proc(1, "disk quota exceeded")

        monkeypatch.setattr(pro_harness, "_run", fake_run)
        with pytest.raises(HarnessError, match="disk quota exceeded"):
            pull_image("some/image:tag")
        assert len(calls) == 1
        assert slept == []

    def test_backoff_values_match_the_declared_schedule_exactly(self, monkeypatch):
        """Not just "some list of length 2" -- the actual seconds, so a
        transposition in DOCKER_PULL_BACKOFF's tuple is caught here rather
        than only showing up as a slower-than-intended sweep."""
        self._never_present(monkeypatch)
        slept = self._no_sleep(monkeypatch)
        stderr = "dial tcp: lookup registry-1.docker.io: no such host"
        monkeypatch.setattr(pro_harness, "_run", lambda cmd, timeout=300: _Proc(1, stderr))
        with pytest.raises(HarnessError):
            pull_image("some/image:tag")
        assert slept == [5.0, 15.0]


class TestPullIsTransientClassifierDirectly:
    """Same discipline as test_agent_retry.py's
    TestBothAgentsAgreeOnClassification: pin the classifier's own
    contract directly, so a future edit to the substring list gets one
    fast, direct failure here instead of only surfacing through the
    slower retry-loop-shaped tests above."""

    @pytest.mark.parametrize("stderr,expected", [
        ("dial tcp: lookup registry-1.docker.io: no such host", True),
        ("temporary failure in name resolution", True),
        ("i/o timeout", True),
        ("net/http: TLS handshake timeout", True),
        ("connection refused", True),
        ("connection reset by peer", True),
        ("unexpected EOF", True),
        ("network is unreachable", True),
        ("manifest unknown: manifest tag does not exist", False),
        ("repository does not exist or may require 'docker login'", False),
        ("unauthorized: authentication required", False),
        ("denied: requested access to the resource is denied", False),
        ("disk quota exceeded", False),
        ("", False),
    ])
    def test_classification_table(self, stderr, expected):
        assert pro_harness._pull_is_transient(stderr) is expected
