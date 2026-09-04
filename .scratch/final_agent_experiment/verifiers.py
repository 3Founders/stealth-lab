"""
Deterministic per-task verifiers for T1/T3/T7, run against a trial's own
disposable worktree BEFORE it is torn down. Each returns
{task_success, deterministic_correctness, verification_quality, details}.
Never a subjective judgment -- every check is a real file read / grep /
pytest invocation against real ground truth computed by ground_truth.py.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ground_truth import t1_ground_truth, t3_candidate_functions, t3_ground_truth_for, t7_ground_truth


def _read_answer(wt_path: Path, name: str = "answer.md") -> str | None:
    p = wt_path / name
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    # tolerate answer.txt too, since agent instructions said answer.md but a
    # model could reasonably use .txt -- check both rather than hard-failing
    # on a filename technicality unrelated to the actual hypothesis under test
    alt = wt_path / (name.rsplit(".", 1)[0] + ".txt")
    return alt.read_text(encoding="utf-8", errors="replace") if alt.exists() else None


def verify_T1(wt_path: Path) -> dict:
    gt = t1_ground_truth(wt_path)
    answer = _read_answer(wt_path)
    if answer is None:
        return {"task_success": False, "deterministic_correctness": 0.0,
                "verification_quality": {"answer_file_found": False},
                "details": {"reason": "no answer.md/answer.txt found in worktree"}}

    found_names = set(re.findall(r"\b([a-z_][a-z0-9_]{2,60})\b", answer.lower()))
    true_names = set(n.lower() for n in gt["tool_function_names"])
    matched = true_names & found_names
    recall = len(matched) / len(true_names) if true_names else 0.0

    resolver_named = gt["shared_resolver"].lower() in answer.lower()

    # success requires BOTH a high-recall function list AND the correct resolver named
    success = recall >= 0.9 and resolver_named
    return {
        "task_success": success,
        "deterministic_correctness": round(recall, 4),
        "verification_quality": {
            "answer_file_found": True,
            "resolver_correctly_named": resolver_named,
            "tool_names_recall": round(recall, 4),
            "true_count": len(true_names), "matched_count": len(matched),
        },
        "details": {"ground_truth": gt, "missing": sorted(true_names - found_names)},
    }


def verify_T1_v2(wt_path: Path) -> dict:
    """T1-v2 (final pre-score remediation, bounded T1/T3 pass): resolver
    identification is the real, required, hypothesis-relevant test;
    exact-name enumeration is now an informational tolerance check, not a
    gating requirement -- see repair_reason in tasks.jsonl for why."""
    gt = t1_ground_truth(wt_path)
    answer = _read_answer(wt_path)
    if answer is None:
        return {"task_success": False, "deterministic_correctness": 0.0,
                "verification_quality": {"answer_file_found": False},
                "details": {"reason": "no answer.md/answer.txt found in worktree"}}

    resolver_named = gt["shared_resolver"].lower() in answer.lower()

    # Informational only: does the answer's reported/implied tool count fall
    # within a generous +/-30% band of the true count? Never gates success.
    true_count = gt["tool_function_count"]
    numbers_in_answer = [int(n) for n in re.findall(r"\b(\d{1,3})\b", answer)]
    count_within_tolerance = any(
        abs(n - true_count) <= max(1, round(true_count * 0.30)) for n in numbers_in_answer
    ) if numbers_in_answer else False

    success = resolver_named  # the ONLY gating requirement
    return {
        "task_success": success,
        "deterministic_correctness": 1.0 if resolver_named else 0.0,
        "verification_quality": {
            "answer_file_found": True,
            "resolver_correctly_named": resolver_named,
            "true_tool_count": true_count,
            "count_within_tolerance_informational_only": count_within_tolerance,
        },
        "details": {"ground_truth": gt},
    }


def verify_T3(wt_path: Path) -> dict:
    candidates = t3_candidate_functions(wt_path)
    answer_text = ""
    for name in ("answer.md", "answer.txt"):
        p = wt_path / name
        if p.exists():
            answer_text += p.read_text(encoding="utf-8", errors="replace")

    # Which candidate function did the agent actually rename? Look for
    # compute_wilson_lower_bound's presence and infer which original name
    # is now ABSENT from the tree (renamed away) among the valid candidates.
    cap_py = wt_path / "backend" / "app" / "services" / "procedure_extraction" / "capability.py"
    cap_text = cap_py.read_text(encoding="utf-8", errors="replace") if cap_py.exists() else ""
    new_name_present = "compute_wilson_lower_bound" in cap_text

    if not new_name_present:
        return {"task_success": False, "deterministic_correctness": 0.0,
                "verification_quality": {"new_name_defined_in_capability_py": False},
                "details": {"candidates": list(candidates.keys()), "answer_text": answer_text[:2000]}}

    renamed_from = None
    for fn in candidates:
        if fn == "compute_wilson_lower_bound":
            continue
        gt = t3_ground_truth_for(wt_path, fn)
        # if the ORIGINAL name has zero remaining references anywhere, and it
        # was a valid multi-caller candidate pre-rename, this is very likely
        # the one that got renamed away
        if gt["defined_in"] is None and gt["external_caller_count"] == 0:
            renamed_from = fn
            break

    if renamed_from is None:
        # fallback: maybe more than one candidate looks fully renamed, or
        # detection is ambiguous -- score conservatively as not clearly correct
        return {"task_success": False, "deterministic_correctness": 0.0,
                "verification_quality": {"new_name_defined_in_capability_py": True,
                                          "renamed_from_detected": False},
                "details": {"candidates": list(candidates.keys()), "answer_text": answer_text[:2000]}}

    # verify NEW name's call-site set matches the OLD function's pre-rename
    # ground truth (computed once, cached by caller) in count/spread
    new_gt = t3_ground_truth_for(wt_path, "compute_wilson_lower_bound")
    pre_rename_gt = candidates[renamed_from]
    expected_caller_count = pre_rename_gt["external_caller_count"]
    actual_caller_count = new_gt["external_caller_count"]
    complete_rename = new_gt["defined_in"] is not None and actual_caller_count >= expected_caller_count

    # run the real test suite for this area
    test_targets = [
        "tests/test_capabilities_offline.py",
        "tests/services/test_procedure_extraction_capability_offline.py",
    ]
    existing_targets = [t for t in test_targets if (wt_path / "backend" / t).exists()]
    tests_passed = None
    test_output = ""
    if existing_targets:
        r = subprocess.run(
            ["python", "-m", "pytest", *existing_targets, "-q"],
            cwd=wt_path / "backend", capture_output=True, text=True, timeout=120,
        )
        tests_passed = (r.returncode == 0)
        test_output = (r.stdout + r.stderr)[-2000:]
    else:
        # search more broadly for any test file mentioning the old or new name
        r = subprocess.run(
            ["grep", "-rl", renamed_from, str(wt_path / "backend" / "tests")],
            capture_output=True, text=True,
        )
        found_tests = [l for l in r.stdout.splitlines() if l.strip()]
        if found_tests:
            r2 = subprocess.run(
                ["python", "-m", "pytest", *found_tests, "-q"],
                cwd=wt_path / "backend", capture_output=True, text=True, timeout=120,
            )
            tests_passed = (r2.returncode == 0)
            test_output = (r2.stdout + r2.stderr)[-2000:]

    success = complete_rename and (tests_passed is not False)  # None (no test found) does not fail it
    return {
        "task_success": success,
        "deterministic_correctness": 1.0 if complete_rename else 0.0,
        "verification_quality": {
            "renamed_from": renamed_from,
            "new_name_defined": new_gt["defined_in"] is not None,
            "expected_caller_count": expected_caller_count,
            "actual_caller_count": actual_caller_count,
            "complete_rename": complete_rename,
            "tests_passed": tests_passed,
        },
        "details": {"new_gt": new_gt, "pre_rename_gt": pre_rename_gt, "test_output_tail": test_output},
    }


def verify_T7(wt_path: Path) -> dict:
    gt = t7_ground_truth(wt_path)
    true_set = set(gt.keys())
    answer = _read_answer(wt_path) or ""
    found_names = set(re.findall(r"\b([a-z_][a-z0-9_]{2,60})\b", answer.lower()))
    true_lower = {n.lower(): n for n in true_set}
    matched = set(true_lower) & found_names
    precision_denom = len(found_names & set(re.findall(r"_[a-z0-9_]+", answer.lower())))
    recall = len(matched) / len(true_set) if true_set else 0.0
    # precision over "private-looking identifiers the answer actually listed"
    private_like_found = {n for n in found_names if n.startswith("_")}
    precision = (len(matched) / len(private_like_found)) if private_like_found else 0.0

    success = recall >= 0.7 and precision >= 0.5
    return {
        "task_success": success,
        "deterministic_correctness": round((recall + precision) / 2, 4),
        "verification_quality": {
            "recall": round(recall, 4), "precision": round(precision, 4),
            "true_count": len(true_set), "matched_count": len(matched),
            "answer_private_identifiers_listed": len(private_like_found),
        },
        "details": {"ground_truth": gt, "missing": sorted(true_set - matched)},
    }


VERIFIERS = {
    "T1": verify_T1, "T3": verify_T3, "T7": verify_T7,
    # T7-v2 uses a separate, dedicated verifier (ground_truth_t7v2_largest_function.py),
    # wired in by the previous pass's own run_pilot.py / calibration scripts.
    "T1-v2": verify_T1_v2,
    # T3-v2 intentionally reuses verify_T3 unchanged -- the defect and its fix
    # live entirely in ground_truth.py's call-site scanner (t3_candidate_functions/
    # t3_ground_truth_for now route through the AST-aware _find_real_call_sites),
    # not in the verifier's own detection logic, so no new function is needed.
    "T3-v2": verify_T3,
}
