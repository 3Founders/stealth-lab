"""
Regression test for the T7-v2 OUTPUT/GRADING CONTRACT defect.

The defect (observed on scored trial T7-v2-A-d51a7347): VERIFIERS had no
"T7-v2" key, so nothing could grade a T7-v2 trial. Every scripted caller
does VERIFIERS[task_id] (KeyError); the orchestrator CLI path passed no
verify_fn at all, so the trial was written with task_success=None -- and
classify_failure then labelled that ungraded trial "model_failure",
asserting a model-quality conclusion from a trial nothing had checked.

Fully deterministic and offline: no model, no MCP, no network. The
worktree is a real temporary directory containing a real
backend/app/services/ tree whose largest function is known by
construction, so the real ground-truth scanner runs against real files.

Run:  python test_t7v2_grading_contract.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import classify_failure  # noqa: E402
from verifiers import VERIFIERS  # noqa: E402

BACKSLASH = chr(92)
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILURES.append(label)


def _make_worktree(tmp: Path) -> tuple[Path, str, str, int]:
    """A real, minimal services/ tree. `big_one` is the largest function
    by physical line span by construction (60 lines vs 12 and 8), so the
    expected grade is derived from the fixture, never hardcoded twice."""
    services = tmp / "backend" / "app" / "services"
    services.mkdir(parents=True)
    (services / "small.py").write_text(
        "def tiny():\n" + "".join("    x%d = %d\n" % (i, i) for i in range(7)),
        encoding="utf-8",
    )
    (services / "mid.py").write_text(
        "class C:\n    def middling(self):\n"
        + "".join("        y%d = %d\n" % (i, i) for i in range(10)),
        encoding="utf-8",
    )
    (services / "big.py").write_text(
        "def big_one():\n" + "".join("    z%d = %d\n" % (i, i) for i in range(59)),
        encoding="utf-8",
    )
    # A subdirectory file that is deliberately OUT of scope even though it
    # is bigger -- proves the fixture exercises the real top-level-only rule.
    sub = services / "nested"
    sub.mkdir()
    (sub / "huge.py").write_text(
        "def huge_but_out_of_scope():\n"
        + "".join("    q%d = %d\n" % (i, i) for i in range(300)),
        encoding="utf-8",
    )
    return tmp, "big_one", "backend/app/services/big.py", 60


def _write_answer(wt: Path, text: str) -> None:
    (wt / "answer.md").write_text(text, encoding="utf-8")


def _clear_answer(wt: Path) -> None:
    for name in ("answer.md", "answer.txt"):
        p = wt / name
        if p.exists():
            p.unlink()


def main() -> int:
    verify = VERIFIERS.get("T7-v2")

    # ---- 0. the wiring defect itself -------------------------------------
    check(verify is not None,
          "0. VERIFIERS has a T7-v2 entry (the missing key that made the "
          "scored trial ungradeable)")
    if verify is None:
        return 1

    tmp_root = Path(tempfile.mkdtemp(prefix="t7v2-contract-"))
    try:
        wt, name, rel, n_lines = _make_worktree(tmp_root)

        # ---- 1. a genuine valid completion is captured and gradeable ------
        _write_answer(
            wt, "# Answer\n\n- function: %s\n- file: %s\n- lines: %d\n" % (name, rel, n_lines))
        v = verify(wt)
        check(v["task_success"] is True,
              "1. a genuine, correct answer.md grades as task_success=True")
        check(v["deterministic_correctness"] == 1.0,
              "1b. a genuine, correct answer.md scores deterministic_correctness=1.0")
        check(classify_failure({"task_success": True}) == "success",
              "1c. a graded success classifies as 'success'")

        # A backslash-separated path is the SAME path, not a looser match.
        _write_answer(wt, "%s in %s -- %d lines\n"
                      % (name, rel.replace("/", BACKSLASH), n_lines))
        check(verify(wt)["task_success"] is True,
              "1d. the same answer written with OS-native path separators "
              "still grades True")

        # ---- 2. a completion without a valid answer still fails -----------
        _clear_answer(wt)
        v = verify(wt)
        check(v["task_success"] is False
              and v["verification_quality"]["answer_file_found"] is False,
              "2. no answer.md at all grades as task_success=False "
              "(the observed trial's real state: files_touched=[])")

        # The exact shape the failing trial is suspected of: the model
        # reports the right answer in its final chat message but never
        # writes the file. final_message is recorded for diagnosis and must
        # NOT be able to grade the trial.
        record = {"task_success": False, "files_touched": [],
                  "final_message": "The largest function is %s in %s at %d lines."
                                   % (name, rel, n_lines)}
        check(verify(wt)["task_success"] is False,
              "2b. a correct answer present only in the model's final message "
              "(never written to answer.md) still fails -- final_message is "
              "diagnostic, never a grading input")
        check(classify_failure(record) == "model_failure",
              "2c. a GRADED false with no error is still 'model_failure'")

        # ---- 3. no false success is introduced ---------------------------
        wrong_cases = [
            ("%s in %s -- %d lines" % (name, rel, n_lines + 1), "wrong line count"),
            ("tiny in %s -- %d lines" % (rel, n_lines), "wrong function name"),
            ("%s in backend/app/services/small.py -- %d lines" % (name, n_lines),
             "wrong file"),
            (name, "name only, no file, no count"),
            ("huge_but_out_of_scope in backend/app/services/nested/huge.py -- 301 lines",
             "the out-of-scope subdirectory function"),
            ("", "empty answer.md"),
        ]
        for text, label in wrong_cases:
            _write_answer(wt, text)
            r = verify(wt)
            check(r["task_success"] is False, "3. no false success: " + label)

        # Partial credit must never round up into a success.
        _write_answer(wt, "%s in %s -- 999 lines" % (name, rel))
        r = verify(wt)
        check(r["task_success"] is False and 0.0 < r["deterministic_correctness"] < 1.0,
              "3b. 2-of-3 components correct scores partial credit and still fails")

        # A substring of a longer number must not satisfy the count check.
        _write_answer(wt, "%s in %s -- %d00 lines" % (name, rel, n_lines))
        check(verify(wt)["task_success"] is False,
              "3c. the true count appearing only inside a longer number does "
              "not satisfy the line-count check")

        # ---- 4. an UNGRADED trial: never a success, never a model failure --
        ungraded = {"task_success": None, "error": None, "budget_exceeded": False,
                    "notes": "step 0 (...): stop_reason=no_tool_call, tool_calls=27"}
        check(classify_failure(ungraded) == "not_graded",
              "4. an ungraded trial (task_success=None) classifies as "
              "'not_graded', not 'model_failure' -- the exact mislabel on "
              "T7-v2-A-d51a7347")
        check(classify_failure({"task_success": None, "error": None,
                                "budget_exceeded": True}) == "timeout",
              "4b. not_graded does not shadow the more specific timeout category")
        check(classify_failure({"task_success": None,
                                "notes": "stop_reason=step_budget"}) == "budget_exhaustion",
              "4c. not_graded does not shadow budget_exhaustion")
        check(classify_failure({"task_success": None,
                                "error": "ValueError: boom"}) == "product_failure",
              "4d. not_graded does not shadow a real error category")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    if FAILURES:
        print("%d CHECK(S) FAILED:" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
