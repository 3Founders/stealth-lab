"""Generic gold-set driver.

Mirrors spec section 2's run_evaluation(task, baseline, treatment, fixture,
config) shape, scoped to component-level gold-set evaluation (no live-agent
baseline/treatment arms here -- that lives in experiments/harness/, see
evaluation/ARCHITECTURE.md). A gold-set test file supplies `run_case`, the
one function that actually calls the real production module under test;
this module only handles loading, timing, JSONL persistence, and aggregate
reporting so that concern isn't reimplemented per subsystem.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .results import EvalResult, write_jsonl

RunCase = Callable[[dict[str, Any]], dict[str, Any]]
# run_case(case) -> {"success": bool, "failure_reason": str | None,
#                     "metrics": dict, ...any EvalResult field overrides}


def load_gold_set(path: Path) -> list[dict[str, Any]]:
    """A gold set is a JSON file: {"cases": [{"id": ..., ...}, ...]}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {path}")
    return cases


def run_gold_set(
    gold_set_name: str,
    cases: list[dict[str, Any]],
    run_case: RunCase,
    *,
    results_path: Path | None = None,
) -> list[EvalResult]:
    """Runs run_case over every gold case, returns one EvalResult per case
    (in input order) and, if results_path is given, appends them as JSONL.
    Never swallows a run_case exception into a false pass -- an exception
    is recorded as a failed result with the exception text as the reason,
    matching the "failure never becomes a silent success" rule that
    threads through the rest of this suite's conventions.
    """
    run_id = uuid.uuid4().hex[:12]
    results: list[EvalResult] = []
    for case in cases:
        case_id = case["id"]
        start = time.perf_counter()
        try:
            outcome = run_case(case)
        except Exception as exc:  # noqa: BLE001 -- convert to a graded failure, not a crash
            outcome = {"success": False, "failure_reason": f"{type(exc).__name__}: {exc}"}
        latency_ms = (time.perf_counter() - start) * 1000
        results.append(
            EvalResult(
                task_id=gold_set_name,
                scenario_id=case_id,
                run_id=run_id,
                baseline_or_treatment=outcome.get("baseline_or_treatment", "n/a"),
                success=bool(outcome.get("success", False)),
                failure_reason=outcome.get("failure_reason"),
                input_tokens=outcome.get("input_tokens", 0),
                output_tokens=outcome.get("output_tokens", 0),
                total_tokens=outcome.get("total_tokens", 0),
                llm_calls=outcome.get("llm_calls", 0),
                tool_calls=outcome.get("tool_calls", 0),
                latency_ms=outcome.get("latency_ms", latency_ms),
                cost=outcome.get("cost", 0.0),
                retries=outcome.get("retries", 0),
                files_touched=outcome.get("files_touched", []),
                verification_result=outcome.get("verification_result"),
                metrics=outcome.get("metrics", {}),
            )
        )
    if results_path is not None:
        write_jsonl(results, results_path, mode="a")
    return results


def success_rate(results: list[EvalResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.success) / len(results)
