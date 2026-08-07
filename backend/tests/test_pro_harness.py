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
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "experiments", "swebench_pro")
)

from pro_harness import (  # noqa: E402
    _grade, build_entryscript, pylist, shell_quote_env, strip_binary_hunks,
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
