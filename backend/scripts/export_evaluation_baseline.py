"""
Regenerates evaluation-results/v1-baseline/{manifest.json,results.jsonl,
summary.md} from the real evaluation-suite test modules under
tests/evaluation/ (spec section 33).

Design choice -- DIRECT CALL, not pytest+JUnit-XML: every gold-set area
already exposes a plain, synchronous, zero-argument module-level function
(run_case / _run_fusion_case / _run_case_generalization / _run_case_transfer,
or -- for the areas with no separate gold JSON, e.g. ingestion/provenance/
concurrency/security -- the pytest test functions themselves are already
synchronous and zero-argument, doing their own asyncio.run() internally).
Importing and calling these directly is strictly more robust than shelling
out to `pytest --junit-xml`: no subprocess, no XML schema to parse, no
version-skew risk between pytest's JUnit output shape and this script, and
it reuses tests.evaluation.harness.gold_runner.run_gold_set's own
EvalResult construction for every gold-JSON-backed area instead of
re-deriving it. The cost is that this script has to know each module's real
function names -- acceptable since those names are a stable, already-read
part of this suite's own source, not pytest internals.

Areas with no separate gold JSON (ingestion, provenance, concurrency,
security) have their test functions called directly and wrapped into
EvalResult by hand: success = no exception raised. Live-DB e2e areas
(provenance, concurrency, the one e2e security test) only run when
DATABASE_URL is set; the manifest and summary both say plainly which areas
ran and which were skipped, rather than silently omitting a section.

Usage (from the backend/ directory, with a populated .env):
    python scripts/export_evaluation_baseline.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()  # same .env the main app reads -- no manual shell export needed

DATABASE_URL = os.environ.get("DATABASE_URL")

from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set
from tests.evaluation.harness.results import EvalResult, build_manifest, write_jsonl, write_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "evaluation" / "fixtures"
OUT_DIR = REPO_ROOT / "evaluation-results" / "v1-baseline"
RESULTS_PATH = OUT_DIR / "results.jsonl"
RUN_ID = uuid.uuid4().hex[:12]


def _call_as_case(area: str, name: str, fn) -> EvalResult:
    """Wraps a zero-arg test function (pytest-style or a bare helper) as one
    EvalResult: success = returned without raising. AssertionError and any
    other exception both count as a graded failure, never a script crash --
    same "failure never becomes a silent success, and never a silent script
    abort" discipline gold_runner.run_gold_set uses."""
    start = time.perf_counter()
    try:
        fn()
        return EvalResult(
            task_id=area, scenario_id=name, run_id=RUN_ID, baseline_or_treatment="n/a",
            success=True, latency_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            task_id=area, scenario_id=name, run_id=RUN_ID, baseline_or_treatment="n/a",
            success=False, failure_reason=f"{type(exc).__name__}: {exc}",
            latency_ms=(time.perf_counter() - start) * 1000,
            metrics={"traceback_tail": traceback.format_exc()[-800:]},
        )


def run_gold_retrieval() -> list[EvalResult]:
    from tests.evaluation.retrieval.test_gold_retrieval_offline import (
        _run_fusion_case,
        test_verified_procedure_not_displaced_by_unverified_locally_close_candidate as safety_test,
    )
    cases = load_gold_set(FIXTURES / "gold_retrieval" / "fusion_cases.json")
    results = run_gold_set("gold_retrieval_fusion", cases, _run_fusion_case, results_path=RESULTS_PATH)
    results.append(_call_as_case(
        "gold_retrieval_safety", "verified_not_displaced_by_unverified_locally_close", safety_test
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
    results.append(_call_as_case(
        "gold_procedures_pin", "incidental_action_not_filtered_gap", incidental_gap_test
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
    from tests.evaluation.evidence.test_gold_evidence_offline import run_case
    cases = load_gold_set(FIXTURES / "gold_evidence" / "cases.json")
    return run_gold_set("gold_evidence", cases, run_case, results_path=RESULTS_PATH)


def run_ingestion_gaps() -> list[EvalResult]:
    import tests.evaluation.ingestion.test_ingestion_gaps_offline as m
    names = [
        "test_out_of_order_timestamps_in_one_batch_both_accepted_independently",
        "test_duplicate_identical_record_within_the_same_batch_second_copy_is_a_conflict",
        "test_late_arriving_event_far_in_the_past_is_accepted_like_any_other_record",
        "test_interleaved_sessions_processed_independently_with_no_cross_contamination",
        "test_very_large_field_value_is_accepted_and_passed_through_unmodified",
        "test_prompt_injection_shaped_text_is_stored_as_inert_string_not_interpreted",
        "test_path_traversal_shaped_value_is_stored_as_inert_string_never_resolved",
        "test_adversarial_unicode_survives_unmodified_no_silent_normalization",
        "test_sql_injection_shaped_value_reaches_the_db_only_as_a_bound_parameter",
    ]
    results = [_call_as_case("ingestion", n, getattr(m, n)) for n in names]
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


def run_security_offline() -> list[EvalResult]:
    import tests.evaluation.security.test_injection_adversarial_offline as m
    names = [
        "test_untrusted_skill_content_reaches_the_llm_prompt_unescaped",
        "test_manipulated_response_without_source_token_echo_is_accepted_as_capability",
        "test_manipulated_response_IS_caught_when_it_echoes_a_concrete_source_token",
        "test_chat_history_import_has_no_llm_call_anywhere_in_this_module",
    ]
    results = [_call_as_case("security_offline", n, getattr(m, n)) for n in names]
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


def run_security_e2e() -> list[EvalResult]:
    if not DATABASE_URL:
        return [EvalResult(
            task_id="security_e2e", scenario_id="test_sql_injection_shaped_claim_content_is_stored_inert_not_executed",
            run_id=RUN_ID, baseline_or_treatment="n/a", success=False,
            failure_reason="SKIPPED: no DATABASE_URL",
        )]
    import tests.evaluation.security.test_injection_adversarial_e2e as m
    results = [_call_as_case(
        "security_e2e", "test_sql_injection_shaped_claim_content_is_stored_inert_not_executed",
        m.test_sql_injection_shaped_claim_content_is_stored_inert_not_executed,
    )]
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


def run_provenance() -> list[EvalResult]:
    if not DATABASE_URL:
        return [EvalResult(
            task_id="provenance", scenario_id="test_precondition_to_claim_to_evidence_to_source_survives_intact",
            run_id=RUN_ID, baseline_or_treatment="n/a", success=False,
            failure_reason="SKIPPED: no DATABASE_URL",
        )]
    import tests.evaluation.provenance.test_full_provenance_chain_e2e as m
    results = [_call_as_case(
        "provenance", "test_precondition_to_claim_to_evidence_to_source_survives_intact",
        m.test_precondition_to_claim_to_evidence_to_source_survives_intact,
    )]
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


def run_concurrency() -> list[EvalResult]:
    names = [
        "test_concurrent_execution_outcomes_on_distinct_contexts_lose_none_under_the_row_lock",
        "test_concurrent_identical_context_key_resubmission_does_not_inflate_distinct_contexts",
        "test_concurrent_candidate_publishes_by_simulated_users_land_as_distinct_uncorrupted_rows",
        "test_claim_supersession_racing_applicability_reads_never_produces_a_torn_result",
        "test_embedding_provider_timeout_during_claim_capture_leaves_no_orphaned_row",
    ]
    if not DATABASE_URL:
        results = [
            EvalResult(task_id="concurrency", scenario_id=n, run_id=RUN_ID, baseline_or_treatment="n/a",
                       success=False, failure_reason="SKIPPED: no DATABASE_URL")
            for n in names
        ]
        write_jsonl(results, RESULTS_PATH, mode="a")
        return results
    import tests.evaluation.concurrency.test_concurrency_chaos_e2e as m
    results = [_call_as_case("concurrency", n, getattr(m, n)) for n in names]
    write_jsonl(results, RESULTS_PATH, mode="a")
    return results


AREAS = [
    ("gold_retrieval", run_gold_retrieval, False),
    ("gold_applicability", run_gold_applicability, False),
    ("gold_procedures", run_gold_procedures, False),
    ("gold_generalization", run_gold_generalization, False),
    ("gold_transfer", run_gold_transfer, False),
    ("gold_evidence", run_gold_evidence, False),
    ("ingestion", run_ingestion_gaps, False),
    ("security_offline", run_security_offline, False),
    ("security_e2e", run_security_e2e, True),
    ("provenance", run_provenance, True),
    ("concurrency", run_concurrency, True),
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
    for area, fn, needs_db in AREAS:
        tag = "" if not needs_db else (" (live DB)" if DATABASE_URL else " (SKIPPED, no DATABASE_URL)")
        print(f"running {area}{tag}...")
        all_results[area] = fn()

    summaries = [_summarize(area, results) for area, results in all_results.items()]

    manifest = build_manifest(
        repo_root=REPO_ROOT,
        corpus_state="v1-baseline-2026-09-02",
        config={
            "model": "n/a -- no live LLM calls in this export (offline/gold-set + live-DB areas only)",
            "embedding_model": "n/a -- gold-set areas use FakeEmbedder stand-ins, no real embedding calls",
            "database_url_present": bool(DATABASE_URL),
        },
        extra={"run_id": RUN_ID, "areas": summaries},
    )
    write_manifest(manifest, OUT_DIR / "manifest.json")

    lines = [
        "# v1-baseline evaluation export",
        "",
        f"Run id `{RUN_ID}`, corpus `v1-baseline-2026-09-02`, generated by "
        "`scripts/export_evaluation_baseline.py`. See `manifest.json` for full "
        "environment detail and `results.jsonl` for the row-level data this "
        "summary is computed from.",
        "",
        "| area | total | passed | failed | skipped |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(f"| {s['area']} | {s['total']} | {s['passed']} | {s['failed']} | {s['skipped']} |")

    lines += ["", "## Headline metrics (from EvalResult.metrics, not hand-typed)", ""]

    def _avg(results, key):
        vals = [r.metrics[key] for r in results if key in r.metrics]
        return sum(vals) / len(vals) if vals else None

    ret = [r for r in all_results["gold_retrieval"] if r.task_id == "gold_retrieval_fusion"]
    lines.append(
        f"- **retrieval** (fusion, n={len(ret)}): "
        f"avg recall@k={_avg(ret, 'recall_at_k'):.3f}, "
        f"avg mrr={_avg(ret, 'mrr'):.3f}, avg ndcg={_avg(ret, 'ndcg'):.3f}"
    )
    app = all_results["gold_applicability"]
    app_predicted = [r.metrics["predicted"] for r in app]
    app_gold = [r.metrics["gold"] for r in app]
    from tests.evaluation.harness import metrics as metrics_mod
    fa = metrics_mod.false_accept_rate(
        app_predicted, app_gold, accept_label="applicable",
        reject_labels={"non_applicable", "partial_match", "unknown", "stale", "superseded", "conflicting"},
    )
    lines.append(f"- **applicability** (n={len(app)}): false_accept_rate={fa:.3f} (safety-critical, target 0.0)")
    proc = [r for r in all_results["gold_procedures"] if r.task_id == "gold_procedures"]
    lines.append(
        f"- **procedure extraction** (n={len(proc)}): "
        f"avg step_precision={_avg(proc, 'step_precision'):.3f}, "
        f"avg precondition_recall={_avg(proc, 'precondition_recall'):.3f}"
    )
    gen = all_results["gold_generalization"]
    gen_pass = sum(1 for r in gen if r.success)
    lines.append(f"- **generalization** (A/B/C, n={len(gen)}): {gen_pass}/{len(gen)} correct")
    xfer = all_results["gold_transfer"]
    xfer_pass = sum(1 for r in xfer if r.success)
    lines.append(
        f"- **transfer** (case D, n={len(xfer)}): {xfer_pass}/{len(xfer)} correct "
        "(2 of 4 are EXPECTED failures -- pinned conservatism gaps, see final-scorecard.md)"
    )
    ev = all_results["gold_evidence"]
    ev_pass = sum(1 for r in ev if r.success)
    lines.append(
        f"- **evidence** (ChatGPT branch cases, n={len(ev)}): {ev_pass}/{len(ev)} match documented "
        "real behavior (includes 2 confirmed-real gaps, see final-scorecard.md)"
    )

    lines += ["", "## Skipped areas", ""]
    skipped_any = False
    for area, results in all_results.items():
        skipped = [r for r in results if r.failure_reason and r.failure_reason.startswith("SKIPPED")]
        if skipped:
            skipped_any = True
            lines.append(f"- `{area}`: {len(skipped)} case(s) skipped -- {skipped[0].failure_reason}")
    if not skipped_any:
        lines.append("None -- DATABASE_URL was available, every area ran for real.")

    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    total_all = sum(s["total"] for s in summaries)
    passed_all = sum(s["passed"] for s in summaries)
    failed_all = sum(s["failed"] for s in summaries)
    skipped_all = sum(s["skipped"] for s in summaries)
    print(f"\n{total_all} cases: {passed_all} passed, {failed_all} failed, {skipped_all} skipped")
    print(f"wrote {RESULTS_PATH}, manifest.json, summary.md under {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
