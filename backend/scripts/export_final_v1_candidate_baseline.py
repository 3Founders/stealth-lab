"""
Regenerates evaluation-results/final-v1-candidate/{manifest.json,results.jsonl,
summary.md} for the HARDENED Final-V1 candidate (core-a/ingestion-testing @
4208b87, merged into this branch) -- spec state B, distinct from
scripts/export_evaluation_baseline.py's frozen historical state-A export
(v1-baseline-2026-09-02, spec state A). This script does NOT modify that one
or its output directory.

Design choice -- DIRECT CALL, matching export_evaluation_baseline.py's own
established reasoning (no subprocess, no JUnit-XML; reuses
tests.evaluation.harness.gold_runner.run_gold_set for gold-JSON areas so that
mechanism isn't reinvented). This suite has grown far beyond gold-JSON areas
since that script was written -- 13 new test files across Phases 2-8 of the
Final-V1 upgrade, mostly plain pytest functions, many @pytest.mark.parametrize,
some needing pytest's `tmp_path` fixture. Rather than hand-listing ~90+
function names per area (error-prone, and silently stale the moment a new
test is added), every NEW area is run via `run_module_area()`, which
AUTO-DISCOVERS every real `test_*` function actually DEFINED in that area's
module (via inspect, filtered by `__module__` so an imported helper alias
is never double-counted), reconstructs any `@pytest.mark.parametrize` cases
from the function's own real pytest marks (introspected, not re-derived by
hand), and synthesizes a real temporary directory for any function that
declares a `tmp_path` parameter (this script does not run under pytest, so
that fixture does not exist for it otherwise). The OLD (pre-Phase-2) gold-JSON
areas keep using gold_runner.load_gold_set/run_gold_set exactly as
export_evaluation_baseline.py already established, and the OLD plain-function
areas (ingestion/security/provenance/concurrency) are also switched to the
same auto-discovery path here for robustness -- it is a strict superset of
"call this named function", not a behavior change for them.

_e2e.py areas are skipped cleanly (one SKIPPED row, not a crash) when
DATABASE_URL is unset, matching every _e2e.py file's own pytestmark
convention in this suite.

One area (retrieval_live_e2e) makes real Voyage embedding-provider API calls
(spec §17) -- everywhere else in this export uses the suite's established
FakeEmbedder stand-ins. No area in this script invokes a live LLM/agent call
or a real experiments/harness/ run -- those stay explicit, separate,
user-triggered steps per the task's own scope limit.

Usage (from the backend/ directory, with DATABASE_URL exported for the full
live-DB run):
    export DATABASE_URL=postgresql://...
    python scripts/export_final_v1_candidate_baseline.py
"""
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()  # same .env the main app reads -- no manual shell export needed

DATABASE_URL = os.environ.get("DATABASE_URL")

from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set
from tests.evaluation.harness.results import EvalResult, build_manifest, write_jsonl, write_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "evaluation" / "fixtures"
OUT_DIR = REPO_ROOT / "evaluation-results" / "final-v1-candidate"
RESULTS_PATH = OUT_DIR / "results.jsonl"
RUN_ID = uuid.uuid4().hex[:12]

SYSTEM_UNDER_TEST_COMMIT = "4208b87cbf8233b2ff1b912f67b625217aa4e42c"
HISTORICAL_BASELINE_COMMIT = "a5dace6ccbe52c7669e13fa0efe8eb17448d05a8"


# ---------------------------------------------------------------------------
# Generic real-function runner: parametrize expansion + sync/async dispatch +
# a synthesized tmp_path, all introspected from the real function object.
# ---------------------------------------------------------------------------
def _expand_parametrize(fn) -> list[tuple[str, dict]]:
    """[(scenario_suffix, kwargs), ...] reconstructed from fn's own real
    pytest.mark.parametrize mark (if any) -- not re-derived from a
    hand-copied list, so it can never drift from what pytest itself would
    actually run."""
    marks = list(getattr(fn, "pytestmark", []) or [])
    param_marks = [m for m in marks if getattr(m, "name", None) == "parametrize"]
    if not param_marks:
        return [("", {})]
    m = param_marks[0]  # this suite's parametrized functions only ever use one
    argnames, argvalues = m.args[0], m.args[1]
    ids = m.kwargs.get("ids")
    if isinstance(argnames, str):
        argnames = [a.strip() for a in argnames.split(",")]
    cases = []
    for i, vals in enumerate(argvalues):
        if not isinstance(vals, (tuple, list)):
            vals = (vals,)
        kwargs = dict(zip(argnames, vals))
        suffix = str(ids[i]) if ids else str(i)
        cases.append((suffix, kwargs))
    return cases


def _invoke(fn, kwargs: dict) -> None:
    if inspect.iscoroutinefunction(fn):
        asyncio.run(fn(**kwargs))
    else:
        fn(**kwargs)


def _run_one(area: str, scenario_id: str, fn, base_kwargs: dict) -> EvalResult:
    """Runs one real (possibly zero-arg) callable as one EvalResult. Never
    swallows an exception into a false pass -- matches gold_runner.run_gold_set
    and export_evaluation_baseline.py's own established discipline."""
    start = time.perf_counter()
    tmp_dir: str | None = None
    try:
        kwargs = dict(base_kwargs)
        sig = inspect.signature(fn)
        if "tmp_path" in sig.parameters:
            tmp_dir = tempfile.mkdtemp(prefix="final_v1_export_")
            kwargs["tmp_path"] = Path(tmp_dir)
        _invoke(fn, kwargs)
        return EvalResult(
            task_id=area, scenario_id=scenario_id, run_id=RUN_ID, baseline_or_treatment="n/a",
            success=True, latency_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            task_id=area, scenario_id=scenario_id, run_id=RUN_ID, baseline_or_treatment="n/a",
            success=False, failure_reason=f"{type(exc).__name__}: {exc}",
            latency_ms=(time.perf_counter() - start) * 1000,
            metrics={"traceback_tail": traceback.format_exc()[-800:]},
        )
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def run_module_area(area: str, module_path: str, *, needs_db: bool) -> list[EvalResult]:
    """Auto-discovers and runs every real test_* function DEFINED in
    `module_path` (not merely imported into it), expanding any
    @pytest.mark.parametrize cases. Skips cleanly (one SKIPPED row) if
    needs_db and DATABASE_URL is unset."""
    if needs_db and not DATABASE_URL:
        results = [EvalResult(
            task_id=area, scenario_id="(all cases)", run_id=RUN_ID, baseline_or_treatment="n/a",
            success=False, failure_reason="SKIPPED: no DATABASE_URL",
        )]
        write_jsonl(results, RESULTS_PATH, mode="a")
        return results

    import importlib
    module = importlib.import_module(module_path)

    fns = [
        (name, obj) for name, obj in inspect.getmembers(module)
        if inspect.isfunction(obj)
        and name.startswith("test_")
        and getattr(obj, "__module__", None) == module.__name__
    ]
    fns.sort(key=lambda x: x[0])

    results: list[EvalResult] = []
    for name, fn in fns:
        for suffix, kwargs in _expand_parametrize(fn):
            scenario_id = f"{name}[{suffix}]" if suffix else name
            results.append(_run_one(area, scenario_id, fn, kwargs))
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


# ---------------------------------------------------------------------------
# Gold-JSON areas -- same mechanism export_evaluation_baseline.py already
# established (gold_runner.load_gold_set/run_gold_set), pointed at THIS
# script's own RESULTS_PATH/RUN_ID rather than the historical script's
# hardcoded ones (importing that script's run_* functions directly would
# silently append to evaluation-results/v1-baseline/results.jsonl instead --
# exactly the "do not overwrite the historical baseline" mistake this
# script exists to avoid).
# ---------------------------------------------------------------------------
def run_gold_retrieval() -> list[EvalResult]:
    from tests.evaluation.retrieval.test_gold_retrieval_offline import (
        _run_fusion_case,
        test_verified_procedure_not_displaced_by_unverified_locally_close_candidate as safety_test,
    )
    cases = load_gold_set(FIXTURES / "gold_retrieval" / "fusion_cases.json")
    results = run_gold_set("gold_retrieval_fusion", cases, _run_fusion_case, results_path=RESULTS_PATH)
    results.append(_run_one(
        "gold_retrieval_safety", "verified_not_displaced_by_unverified_locally_close", safety_test, {}
    ))
    write_jsonl(results[-1:], RESULTS_PATH, mode="a")
    return results


def run_gold_applicability() -> list[EvalResult]:
    from tests.evaluation.applicability.test_gold_applicability_offline import run_case
    cases = load_gold_set(FIXTURES / "gold_applicability" / "cases.json")
    return run_gold_set("gold_applicability", cases, run_case, results_path=RESULTS_PATH)


def run_gold_procedures() -> list[EvalResult]:
    from tests.evaluation.learning.test_gold_procedures_offline import (
        run_case,
        test_incidental_action_gap_is_pinned_not_silently_fixed as incidental_gap_test,
    )
    cases = load_gold_set(FIXTURES / "gold_procedures" / "cases.json")
    results = run_gold_set("gold_procedures", cases, run_case, results_path=RESULTS_PATH)
    results.append(_run_one(
        "gold_procedures_pin", "incidental_action_not_filtered_gap", incidental_gap_test, {}
    ))
    write_jsonl(results[-1:], RESULTS_PATH, mode="a")
    return results


def run_gold_generalization() -> list[EvalResult]:
    from tests.evaluation.generalization.test_gold_generalization_offline import _run_case_generalization
    cases = load_gold_set(FIXTURES / "gold_generalization" / "cases.json")
    return run_gold_set("gold_generalization", cases, _run_case_generalization, results_path=RESULTS_PATH)


def run_gold_transfer() -> list[EvalResult]:
    from tests.evaluation.generalization.test_gold_generalization_offline import _run_case_transfer
    cases = load_gold_set(FIXTURES / "gold_transfer" / "cases.json")
    return run_gold_set("gold_transfer", cases, _run_case_transfer, results_path=RESULTS_PATH)


def run_gold_evidence() -> list[EvalResult]:
    """Phase 5 updated this gold set (and its expectations) to the FIXED
    ChatGPT-branch behavior -- see git history on
    tests/evaluation/fixtures/gold_evidence/cases.json. Same mechanism,
    now-fixed-behavior data."""
    from tests.evaluation.evidence.test_gold_evidence_offline import run_case
    cases = load_gold_set(FIXTURES / "gold_evidence" / "cases.json")
    return run_gold_set("gold_evidence", cases, run_case, results_path=RESULTS_PATH)


# ---------------------------------------------------------------------------
# Every area, old and new. (area_name, kind, spec) where kind is "gold" (one
# of the functions above) or "module" (auto-discovery via run_module_area).
# ---------------------------------------------------------------------------
GOLD_AREAS = [
    ("gold_retrieval", run_gold_retrieval, False),
    ("gold_applicability", run_gold_applicability, False),
    ("gold_procedures", run_gold_procedures, False),
    ("gold_generalization", run_gold_generalization, False),
    ("gold_transfer", run_gold_transfer, False),
    ("gold_evidence", run_gold_evidence, False),
]

MODULE_AREAS = [
    # --- pre-existing, pre-Phase-2 (now via auto-discovery for robustness) ---
    ("ingestion", "tests.evaluation.ingestion.test_ingestion_gaps_offline", False),
    ("security_offline", "tests.evaluation.security.test_injection_adversarial_offline", False),
    ("security_e2e", "tests.evaluation.security.test_injection_adversarial_e2e", True),
    ("provenance", "tests.evaluation.provenance.test_full_provenance_chain_e2e", True),
    ("concurrency", "tests.evaluation.concurrency.test_concurrency_chaos_e2e", True),
    # --- Phase 2: Problem/Benchmark/Solution/Evaluation ---
    ("product_model_offline", "tests.evaluation.product_model.test_gold_product_model_offline", False),
    ("product_model_e2e", "tests.evaluation.product_model.test_gold_product_model_e2e", True),
    # --- Phase 3: durable retry/resume ---
    ("durable_offline", "tests.evaluation.durable.test_gold_durable_offline", False),
    ("durable_e2e", "tests.evaluation.durable.test_gold_durable_e2e", True),
    # --- Phase 4: implementation descriptor ---
    ("implementation_descriptor_offline",
     "tests.evaluation.implementation.test_gold_implementation_descriptor_offline", False),
    # --- Phase 5: ingestion source matrix (§9/§10 fixed-behavior tests
    # already live in gold_evidence/security_offline above) ---
    ("ingestion_sources_repo_doc_offline",
     "tests.evaluation.ingestion_sources.test_repo_doc_adapters_offline", False),
    ("ingestion_sources_history_offline",
     "tests.evaluation.ingestion_sources.test_history_sources_offline", False),
    ("ingestion_sources_admission_e2e",
     "tests.evaluation.ingestion_sources.test_source_admission_e2e", True),
    # --- Phase 6: cross-user privacy ---
    ("privacy_e2e", "tests.evaluation.privacy.test_cross_user_privacy_e2e", True),
    # --- Phase 7: staleness/Evaluation connection + failure learning ---
    ("staleness_evaluation_connection_e2e",
     "tests.evaluation.staleness_and_failure.test_staleness_evaluation_connection_e2e", True),
    ("failure_learning_cross_checks_e2e",
     "tests.evaluation.staleness_and_failure.test_failure_learning_cross_checks_e2e", True),
    # --- Phase 8a: real live-DB retrieval (real Voyage embedding calls) ---
    ("retrieval_live_e2e", "tests.evaluation.retrieval.test_live_retrieval_e2e", True),
    # --- Phase 8b: durable-layer load/chaos ---
    ("concurrency_durable_e2e", "tests.evaluation.concurrency.test_durable_concurrency_chaos_e2e", True),
]


def _summarize(area: str, results: list[EvalResult]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.success)
    skipped = sum(1 for r in results if r.failure_reason and r.failure_reason.startswith("SKIPPED"))
    return {
        "area": area, "total": total, "passed": passed,
        "failed": total - passed - skipped, "skipped": skipped,
    }


def main() -> int:
    if RESULTS_PATH.exists():
        RESULTS_PATH.unlink()  # fresh export each run, not an append across runs
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_results: dict[str, list[EvalResult]] = {}

    for area, fn, needs_db in GOLD_AREAS:
        tag = "" if not needs_db else (" (live DB)" if DATABASE_URL else " (SKIPPED, no DATABASE_URL)")
        print(f"running {area}{tag}...")
        all_results[area] = fn()

    for area, module_path, needs_db in MODULE_AREAS:
        tag = "" if not needs_db else (" (live DB)" if DATABASE_URL else " (SKIPPED, no DATABASE_URL)")
        print(f"running {area}{tag}...")
        try:
            all_results[area] = run_module_area(area, module_path, needs_db=needs_db)
        except Exception as exc:  # noqa: BLE001 -- one area's import/collection failure must not abort the export
            row = EvalResult(
                task_id=area, scenario_id="(module import/collection)", run_id=RUN_ID,
                baseline_or_treatment="n/a", success=False,
                failure_reason=f"{type(exc).__name__}: {exc}",
                metrics={"traceback_tail": traceback.format_exc()[-800:]},
            )
            write_jsonl([row], RESULTS_PATH, mode="a")
            all_results[area] = [row]

    summaries = [_summarize(area, results) for area, results in all_results.items()]

    manifest = build_manifest(
        repo_root=REPO_ROOT,
        corpus_state=f"final-v1-candidate-{date.today().isoformat()}",
        system_under_test_commit=SYSTEM_UNDER_TEST_COMMIT,
        evaluation_harness_commit=None,  # defaults to this repo's own current HEAD
        historical_baseline_commit=HISTORICAL_BASELINE_COMMIT,
        branch="evaluation-suite",
        final_v1_tag=None,  # does not exist yet -- spec state C, see task §25
        config={
            "model": "n/a -- no live agent/LLM calls in this export "
                     "(offline/gold-set + live-DB areas, plus one real-embedding retrieval area)",
            "embedding_model": "real Voyage embeddings used ONLY in retrieval_live_e2e "
                                "(tests.evaluation.retrieval.test_live_retrieval_e2e, spec §17); "
                                "every other area uses this suite's established FakeEmbedder stand-in",
            "database_url_present": bool(DATABASE_URL),
        },
        extra={"run_id": RUN_ID, "areas": summaries},
    )
    write_manifest(manifest, OUT_DIR / "manifest.json")

    lines = [
        "# final-v1-candidate evaluation export",
        "",
        f"Run id `{RUN_ID}`, system-under-test `{SYSTEM_UNDER_TEST_COMMIT}` "
        "(`core-a/ingestion-testing`), evaluation-harness commit = this export's own "
        "generating commit (see manifest.json), historical-baseline commit "
        f"`{HISTORICAL_BASELINE_COMMIT}` (unchanged, unaffected by this export). "
        "Generated by `scripts/export_final_v1_candidate_baseline.py`. See `manifest.json` "
        "for full environment detail and `results.jsonl` for the row-level data this "
        "summary is computed from. Narrative interpretation (what these numbers MEAN, "
        "including the real findings this pass surfaced) lives in `final-scorecard.md`, "
        "not here -- this file is the mechanical rollup only.",
        "",
        "| area | total | passed | failed | skipped |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(f"| {s['area']} | {s['total']} | {s['passed']} | {s['failed']} | {s['skipped']} |")

    lines += ["", "## Skipped areas", ""]
    skipped_any = False
    for area, results in all_results.items():
        skipped = [r for r in results if r.failure_reason and r.failure_reason.startswith("SKIPPED")]
        if skipped:
            skipped_any = True
            lines.append(f"- `{area}`: {len(skipped)} case(s) skipped -- {skipped[0].failure_reason}")
    if not skipped_any:
        lines.append("None -- DATABASE_URL was available, every area ran for real.")

    lines += ["", "## Areas with a real, non-test failure (needs investigation before trusting green elsewhere)", ""]
    failed_any = False
    for area, results in all_results.items():
        failed = [r for r in results if not r.success and not (r.failure_reason or "").startswith("SKIPPED")]
        if failed:
            failed_any = True
            lines.append(f"- `{area}`: {len(failed)} failing case(s), e.g. `{failed[0].scenario_id}` -- {failed[0].failure_reason}")
    if not failed_any:
        lines.append("None.")

    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    total_all = sum(s["total"] for s in summaries)
    passed_all = sum(s["passed"] for s in summaries)
    failed_all = sum(s["failed"] for s in summaries)
    skipped_all = sum(s["skipped"] for s in summaries)
    print(f"\n{total_all} cases: {passed_all} passed, {failed_all} failed, {skipped_all} skipped")
    print(f"wrote {RESULTS_PATH}, manifest.json, summary.md under {OUT_DIR}")
    return 0 if failed_all == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
