"""Self-test for the gold-set harness itself (metrics.py, gold_runner.py,
results.py). Every downstream gold-set suite (Phase 2+) depends on these
being correct, so they get their own direct behavioral tests rather than
only being exercised indirectly.
"""
from __future__ import annotations

import json

from tests.evaluation.harness import metrics
from tests.evaluation.harness.gold_runner import (
    load_gold_set,
    run_gold_set,
    success_rate,
)
from tests.evaluation.harness.results import (
    EvalResult,
    build_manifest,
    read_jsonl,
    write_jsonl,
)


def test_recall_at_k_counts_any_relevant_id_in_top_k():
    ranked = ["a", "b", "c", "d"]
    assert metrics.recall_at_k(ranked, {"c"}, k=3) == 1.0
    assert metrics.recall_at_k(ranked, {"d"}, k=3) == 0.0
    assert metrics.recall_at_k(ranked, {"a", "d"}, k=3) == 0.5


def test_mrr_is_reciprocal_of_first_hit_rank():
    assert metrics.mrr(["x", "y", "z"], {"z"}) == 1 / 3
    assert metrics.mrr(["x", "y", "z"], {"x"}) == 1.0
    assert metrics.mrr(["x", "y", "z"], {"nope"}) == 0.0


def test_ndcg_penalizes_relevant_result_ranked_lower():
    high = metrics.ndcg(["rel", "irrel"], {"rel"})
    low = metrics.ndcg(["irrel", "rel"], {"rel"})
    assert high > low
    assert metrics.ndcg([], {"rel"}) == 0.0


def test_false_accept_rate_only_counts_should_reject_cases():
    predicted = ["applicable", "applicable", "applicable", "unknown"]
    gold =      ["applicable", "stale",      "applicable", "stale"]
    # two should-reject cases (stale, stale): one wrongly accepted, one correctly rejected
    assert metrics.false_accept_rate(
        predicted, gold, accept_label="applicable", reject_labels={"stale", "conflicting"}
    ) == 0.5


def test_false_reject_rate_only_counts_should_accept_cases():
    predicted = ["applicable", "unknown", "applicable"]
    gold =      ["applicable", "applicable", "applicable"]
    assert metrics.false_reject_rate(predicted, gold, accept_label="applicable") == 1 / 3


def test_abstention_accuracy_credits_correct_unknowns_only():
    predicted = ["unknown", "applicable", "unknown"]
    gold =      ["unknown", "unknown",    "unknown"]
    assert metrics.abstention_accuracy(predicted, gold, unknown_label="unknown") == 2 / 3


def test_ordering_accuracy_is_1_when_relative_order_preserved():
    gold_steps = ["clone", "install", "migrate", "test"]
    predicted_same_order = ["install", "clone", "migrate", "test", "extra"]
    # "install" and "clone" are swapped relative to gold -> not all pairs agree
    assert metrics.ordering_accuracy(gold_steps, gold_steps) == 1.0
    assert metrics.ordering_accuracy(predicted_same_order, gold_steps) < 1.0


def test_gold_runner_records_a_thrown_exception_as_a_graded_failure_not_a_crash():
    cases = [{"id": "c1"}, {"id": "c2"}]

    def run_case(case):
        if case["id"] == "c1":
            raise RuntimeError("boom")
        return {"success": True}

    results = run_gold_set("demo_set", cases, run_case)
    assert len(results) == 2
    c1 = next(r for r in results if r.scenario_id == "c1")
    assert c1.success is False
    assert "boom" in c1.failure_reason
    c2 = next(r for r in results if r.scenario_id == "c2")
    assert c2.success is True
    assert success_rate(results) == 0.5


def test_load_gold_set_rejects_duplicate_case_ids(tmp_path):
    bad = tmp_path / "gold.json"
    bad.write_text(json.dumps({"cases": [{"id": "x"}, {"id": "x"}]}), encoding="utf-8")
    try:
        load_gold_set(bad)
        assert False, "expected ValueError for duplicate ids"
    except ValueError:
        pass


def test_jsonl_round_trip(tmp_path):
    out = tmp_path / "results.jsonl"
    results = [
        EvalResult(task_id="t", scenario_id="s1", run_id="r", baseline_or_treatment="n/a", success=True),
        EvalResult(task_id="t", scenario_id="s2", run_id="r", baseline_or_treatment="n/a", success=False, failure_reason="x"),
    ]
    write_jsonl(results, out)
    rows = read_jsonl(out)
    assert len(rows) == 2
    assert rows[0]["scenario_id"] == "s1"
    assert rows[1]["success"] is False


def test_manifest_captures_commit_and_config(tmp_path):
    manifest = build_manifest(
        repo_root=tmp_path, corpus_state="v1-baseline-2026-09-02", config={"model": "n/a"}
    )
    assert "generated_at" in manifest
    assert manifest["corpus_state"] == "v1-baseline-2026-09-02"
    assert manifest["config"] == {"model": "n/a"}
