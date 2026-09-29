"""Offline: the verified-solutions pipeline -- row gates, license source, credit, extraction contract, benchmarks."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.ingest.common.benchmarks import TaskTests
from app.ingest.verified import extract as ex
from app.ingest.verified.pipeline import credit, gate_row, license_expression
from app.ingest.verified.sources import SOURCES, to_task


def _row(**kw):
    base = {"instance_id": "o__r-1", "repo": "o/r", "base_commit": "a" * 40, "problem_statement": "Parser drops lines",
            "patch": "diff --git a/p.py b/p.py", "test_patch": "diff --git a/t.py b/t.py",
            "FAIL_TO_PASS": ["t.py::test_a"], "PASS_TO_PASS": ["t.py::test_b"], "FAIL_TO_FAIL": [], "PASS_TO_FAIL": [],
            "license_name": "MIT License", "docker_image": "img:1", "install_config": {"test_cmd": "pytest"},
            "meta": {"llm_score": {"test_score": 1}}, "hints_text": ""}
    return {**base, **kw}


class _Held:
    scored_repos = ("django/django",)

    def is_held_out(self, x):
        return x == "held__x-1"


@pytest.mark.parametrize("override,reason", [
    (dict(patch=""), "missing_patch"), (dict(test_patch=" "), "missing_test_patch"),
    (dict(FAIL_TO_PASS=[]), "missing_fail_to_pass"),
    (dict(PASS_TO_FAIL=["y"]), "gold_patch_breaks_tests"), (dict(instance_id="held__x-1"), "held_out_instance"),
    (dict(repo="Django/Django"), "held_out_repo"),
])
def test_row_gates(override, reason):
    task = to_task(SOURCES["swe-rebench"], _row(**override))
    assert gate_row(task, _Held())[0] == reason


def test_a_good_row_passes_and_maps_every_field():
    task = to_task(SOURCES["swe-rebench"], _row())
    assert gate_row(task, _Held()) is None
    assert task.docker_image == "img:1" and task.test_cmd == "pytest" and task.fail_to_pass == ("t.py::test_a",)
    assert task.item_key == "swe-rebench:o__r-1" and task.dedup_key == "swe-solution:o__r-1"


def test_one_task_has_one_identity_across_sources():
    a = to_task(SOURCES["swe-rebench"], _row())
    b = to_task(SOURCES["swe-bench-extra"], {**_row(), "license": "mit"})
    assert a.dedup_key == b.dedup_key and a.item_key != b.item_key


def test_license_is_read_from_the_row_or_from_github():
    assert license_expression(to_task(SOURCES["swe-rebench"], _row()), SOURCES["swe-rebench"]) == ("MIT", "row_github_name")
    v2 = SOURCES["swe-rebench-v2"]
    assert license_expression(to_task(v2, {**_row(), "license": "Apache-2.0"}), v2) == ("Apache-2.0", "row_spdx")
    assert license_expression(to_task(v2, {**_row(), "license": "custom-check-github"}), v2) == (None, "github")
    gym = SOURCES["swe-gym"]
    assert license_expression(to_task(gym, _row()), gym) == (None, "github")
    assert license_expression(to_task(SOURCES["swe-rebench"], _row(license_name="BSD")), SOURCES["swe-rebench"])[0] is None


def test_a_task_without_an_image_gets_no_benchmark():
    t = TaskTests("swe:x", "hf:d", "o/r", "c", None, "pytest", ("a",), (), "diff")
    assert not t.runnable
    assert TaskTests("swe:x", "hf:d", "o/r", "c", "img", "pytest", ("a",), (), "diff").runnable
    # tests that fail with and without the fix are known failures, not a reason to refuse the task or its Benchmark
    assert TaskTests("swe:x", "hf:d", "o/r", "c", "img", "pytest", ("a",), (), "diff", fail_to_fail=("f",)).runnable
    assert not TaskTests("swe:x", "hf:d", "o/r", "c", "img", "pytest", ("a",), (), "diff", pass_to_fail=("p",)).runnable


def test_credit_names_repository_license_and_dataset():
    task = to_task(SOURCES["swe-rebench"], _row())
    c = credit(task, SOURCES["swe-rebench"], "MIT")
    assert "o/r" in c["notice"] and "MIT" in c["notice"] and "nebius/SWE-rebench" in c["notice"]


def _client(reply):
    class Completions:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)
    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


GOOD = {"name": "Make relative paths POSIX-style on every platform",
        "steps": [{"do": "Find where the relative path is built", "role": "plan", "check": ""},
                  {"do": "Replace OS separators with forward slashes", "role": "edit", "check": ""},
                  {"do": "Run the failing test", "role": "verify", "check": "pytest t.py::test_a"}],
        "preconditions": ["Paths are built with os.path"], "pitfalls": ["Using str.replace on '\\\\' only"],
        "facts": ["os.path.relpath returns backslashes on Windows"]}


def _run(reply):
    return asyncio.run(ex.extract(_client(reply), "m", goal="g", repo="o/r", language="python", issue="i", hints="",
                                  patch="p", tests=("t",)))


def test_extraction_accepts_the_contract_and_fenced_json():
    assert _run(json.dumps(GOOD)).steps[-1].role == "verify"
    assert _run("```json\n" + json.dumps(GOOD) + "\n```").name.startswith("Make relative")


@pytest.mark.parametrize("bad,msg", [
    ({**GOOD, "steps": GOOD["steps"][:2]}, "last step is not the verification"),
    ({**GOOD, "steps": [GOOD["steps"][2]]}, "schema"),
    ({**GOOD, "extra": 1}, "schema"),
])
def test_extraction_refuses_what_breaks_the_contract(bad, msg):
    with pytest.raises(ex.ExtractionFailed, match=msg):
        _run(json.dumps(bad))


def test_extraction_refuses_non_json():
    with pytest.raises(ex.ExtractionFailed, match="not JSON"):
        _run("I think the fix is to change the separator.")


def test_always_failing_tests_do_not_reject_a_task():
    task = to_task(SOURCES["swe-rebench"], _row(FAIL_TO_FAIL=["t.py::needs_network"]))
    assert gate_row(task, _Held()) is None
    assert task.fail_to_fail == ("t.py::needs_network",)
