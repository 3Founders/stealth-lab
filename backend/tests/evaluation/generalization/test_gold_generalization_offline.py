"""Gold-labeled generalization + transfer benchmark (spec section 9).

backend/tests/test_synthesis_generalization_offline.py already proves each
compatibility gate and the level-computation function is individually
correct (contradiction detection, structural alignment, precondition
intersection, scope union, generalization-level bands). This file is the
missing piece the audit found: an INTEGRATION-level gold benchmark that
orchestrates those same real gates, in the same order
`synthesize_procedure()` uses, over curated multi-episode bundles with a
known-correct merge/refuse/level answer -- computing spec section 9's named
metrics (generalization precision/recall, over/under-generalization rate,
transfer success, unsafe transfer rate) rather than only proving each gate
works in isolation.

`_evaluate_bundle()` below is NOT a second implementation of the merge
logic -- every judgment call (contradiction, structural alignment,
real-variation detection, generalization-level banding, slot-binder
agreement) is delegated to the actual functions
app.services.procedure_extraction.synthesis.synthesize_procedure() calls
internally. It exists only because that function itself is async and
DB-bound (build_episode_evidence, derive_preconditions, derive_scope all
take a real asyncpg.Pool) -- this file constructs the SAME inputs those
derivations would have produced (StepGroup skeletons, Predicate lists,
scope dicts, SlotSpecs) directly, so the pure gates can run offline.

Case D (backend/tests/evaluation/fixtures/gold_transfer/) is the genuinely
new territory: boundary variation across repos/package-versions/
implementations. Per the audit, this was expected to surface a real,
already-documented gap in synthesis.py (no true multi-sequence alignment;
exact-value-only predicate equality with no compatible-range notion) --
see the assertions at the bottom of test_gold_transfer_cases, which PIN the
current real numbers as a regression baseline rather than asserting 100%
transfer success. If those numbers improve later (synthesis.py becomes
less conservative), update the pinned values and note why in the commit;
if they get worse, that's a real regression this test exists to catch.
"""
from __future__ import annotations

from pathlib import Path

from app.services.dedup import complete_linkage_clusters
from app.services.procedure_extraction.derive import StepGroup
from app.services.procedure_extraction.schema import Predicate, SlotSpec
from app.services.procedure_extraction.synthesis import (
    TOOL_SEQUENCE_SIMILARITY_THRESHOLD,
    _find_predicate_contradiction,
    _has_real_variation,
    _merge_slots,
    _tool_sequence_similarity,
    compute_generalization_level,
)

from tests.evaluation.harness import metrics as metrics_mod
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set, success_rate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _evaluate_bundle(episodes: dict) -> dict:
    """Runs the real synthesis.py compatibility gates + level computation
    over a constructed multi-episode bundle, in the same order
    synthesize_procedure() does. Returns {"merged", "refusal_gate",
    "generalization_level"}."""
    episode_ids = list(episodes.keys())

    skeletons = {
        eid: [StepGroup(tool, count) for tool, count in ep["skeleton"]]
        for eid, ep in episodes.items()
    }
    tool_names = {eid: [g.tool_name for g in skel] for eid, skel in skeletons.items()}

    def _sim(a: str, b: str) -> float:
        return _tool_sequence_similarity(tool_names[a], tool_names[b])

    clusters = complete_linkage_clusters(episode_ids, _sim, TOOL_SEQUENCE_SIMILARITY_THRESHOLD)
    main_cluster = max(clusters, key=len)
    if len(main_cluster) < len(episode_ids):
        return {"merged": False, "refusal_gate": "tool_sequence_alignment", "generalization_level": None}

    preconditions_by_episode = {
        eid: [Predicate(subject=s, predicate=p, object=o) for s, p, o in ep["preconditions"]]
        for eid, ep in episodes.items()
    }
    if _find_predicate_contradiction(preconditions_by_episode) is not None:
        return {"merged": False, "refusal_gate": "predicate_contradiction", "generalization_level": None}

    slots_by_episode = {
        eid: [SlotSpec(**s) for s in ep.get("slots", [])] for eid, ep in episodes.items()
    }
    slot_refusal, merged_slots = _merge_slots(slots_by_episode)
    if slot_refusal:
        return {"merged": False, "refusal_gate": "slot_binder_disagreement", "generalization_level": None}

    scopes = [ep.get("scope", {}) for ep in episodes.values()]
    has_slots = any(s.binder != "literal" for s in merged_slots)
    real_variation = _has_real_variation(skeletons, scopes)
    level = compute_generalization_level(
        n_episodes=len(episode_ids), has_slots=has_slots, real_variation_found=real_variation,
    )
    return {"merged": True, "refusal_gate": None, "generalization_level": level}


# ---------------------------------------------------------------------
# gold_generalization: cases A (repeat), B (compatible variation),
# C (contradictory) -- all expected to be handled correctly today.
# ---------------------------------------------------------------------

def _run_case_generalization(case: dict) -> dict:
    actual = _evaluate_bundle(case["episodes"])
    expected = case["expected"]
    matches = (
        actual["merged"] == expected["should_merge"]
        and actual["refusal_gate"] == expected.get("refusal_gate")
        and actual["generalization_level"] == expected.get("generalization_level")
    )
    return {
        "success": matches,
        "failure_reason": None if matches else f"expected {expected}, got {actual}",
        "metrics": {
            "actual_merged": actual["merged"],
            "actual_refusal_gate": actual["refusal_gate"],
            "actual_generalization_level": actual["generalization_level"],
            "case_type": case["case_type"],
        },
    }


def test_gold_generalization_cases():
    cases = load_gold_set(FIXTURES / "gold_generalization" / "cases.json")
    results = run_gold_set("gold_generalization", cases, _run_case_generalization)

    failures = [(r.scenario_id, r.failure_reason) for r in results if not r.success]
    assert not failures, f"generalization gold cases diverged from expected: {failures}"

    predicted = ["merge" if r.metrics["actual_merged"] else "refuse" for r in results]
    gold = ["merge" if c["expected"]["should_merge"] else "refuse" for c in cases]
    precision, recall = metrics_mod.precision_recall(predicted, gold, positive_label="merge")
    over_generalization_rate = sum(
        1 for r, c in zip(results, cases)
        if r.metrics["actual_merged"] and not c["expected"]["should_merge"]
    ) / len(cases)
    under_generalization_rate = sum(
        1 for r, c in zip(results, cases)
        if not r.metrics["actual_merged"] and c["expected"]["should_merge"]
    ) / len(cases)

    # A/B/C are the already-well-tested territory (backend/tests/
    # test_synthesis_generalization_offline.py proves each gate in
    # isolation) -- this integration benchmark is expected to be clean.
    assert precision == 1.0
    assert recall == 1.0
    assert over_generalization_rate == 0.0
    assert under_generalization_rate == 0.0
    assert success_rate(results) == 1.0


# ---------------------------------------------------------------------
# gold_transfer: case D, boundary variation. Genuinely new territory --
# see module docstring. Pins today's real (imperfect) numbers.
# ---------------------------------------------------------------------

def _run_case_transfer(case: dict) -> dict:
    actual = _evaluate_bundle(case["episodes"])
    expected_transfer = case["expected"]["should_transfer"]
    matches = actual["merged"] == expected_transfer
    if matches:
        reason = None
    elif actual["merged"]:
        reason = f"gold says should_transfer={expected_transfer}, system merged"
    else:
        reason = (
            f"gold says should_transfer={expected_transfer}, system refused "
            f"({actual['refusal_gate']})"
        )
    return {
        "success": matches,
        "failure_reason": reason,
        "metrics": {"actual_merged": actual["merged"], "refusal_gate": actual["refusal_gate"]},
    }


def test_gold_transfer_cases():
    cases = load_gold_set(FIXTURES / "gold_transfer" / "cases.json")
    results = run_gold_set("gold_transfer", cases, _run_case_transfer)

    should_transfer_cases = [
        (r, c) for r, c in zip(results, cases) if c["expected"]["should_transfer"]
    ]
    should_not_transfer_cases = [
        (r, c) for r, c in zip(results, cases) if not c["expected"]["should_transfer"]
    ]
    transfer_success = (
        sum(1 for r, _ in should_transfer_cases if r.metrics["actual_merged"])
        / len(should_transfer_cases)
    )
    unsafe_transfer_rate = (
        sum(1 for r, _ in should_not_transfer_cases if r.metrics["actual_merged"])
        / len(should_not_transfer_cases)
    )

    # D1 (cross-repo, same real facts) and D2 (genuinely different package
    # version, correctly refused) both behave correctly today.
    d1 = next(r for r in results if r.scenario_id == "D1-cross-repo-same-real-facts-safe-transfer")
    d2 = next(r for r in results if r.scenario_id == "D2-different-package-version-unsafe-transfer")
    assert d1.success, d1.failure_reason
    assert d2.success, d2.failure_reason

    # D3 (different implementation, same method) and D4 (patch-level
    # version bump) are EXPECTED to fail today -- this documents two real,
    # already-known-in-the-module's-own-docstring conservatism gaps:
    #   - no true multi-sequence alignment (D3: a materially different but
    #     equally valid tool-call pattern is refused as "a different
    #     method", never recognized as the same one via a smarter aligner)
    #   - exact-value-only predicate contradiction, no compatible-range
    #     notion (D4: a non-breaking patch version bump is treated
    #     identically to a genuinely breaking major-version change)
    # These are discovered production gaps, not implemented against here
    # (spec: no new V1 features) -- see evaluation-results/final-scorecard.md.
    d3 = next(r for r in results if r.scenario_id == "D3-different-implementation-same-method")
    d4 = next(r for r in results if r.scenario_id == "D4-patch-level-version-difference-should-still-transfer")
    assert not d3.success, "expected D3 to currently fail (documents the multi-sequence-alignment gap)"
    assert not d4.success, "expected D4 to currently fail (documents the exact-value-predicate gap)"

    # Pinned regression baseline: 1 of 3 should-transfer cases actually
    # transfers (D1 only); the system never unsafely transfers (0 of 1
    # should-not-transfer cases wrongly merged). Safety is intact;
    # transfer recall on boundary variation is genuinely low.
    assert transfer_success == 1 / 3
    assert unsafe_transfer_rate == 0.0
