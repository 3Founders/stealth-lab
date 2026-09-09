"""Gold-set evaluation for procedure extraction (spec section 7), run
against the real, no-LLM production extraction path: DeterministicExtractor
(app/services/procedure_extraction/strategies.py). This is the actual
fallback strategy production uses when no LLM client is configured or the
model call fails -- not a test-only stub.

GroundedHybridExtractor (the LLM abstraction path -- capability_statement
and generalized step phrasing) is intentionally out of scope here: this
suite doesn't spend live LLM budget this pass (see evaluation/README.md's
Known limitations). What IS fully offline-testable, and is exactly what
this gold set measures, is everything strategies.py's own docstring calls
"a real derivation against real state": the step skeleton, preconditions
(with provenance/claim_id), scope, and failure conditions.

FakePool below reuses the exact technique test_derive_offline.py already
established (a stub matching project_state()'s own row shape) -- not a new
pattern.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.strategies import DeterministicExtractor
from tests.evaluation.harness import metrics
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set, success_rate

GOLD_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "gold_procedures" / "cases.json"


def _claim_row(row_id, predicate, obj, subject):
    return {
        "id": row_id,
        "properties": {"subject": subject, "predicate": predicate, "object": obj},
        "t_valid": None,
        "t_invalid": None,
    }


class FakePool:
    """Stands in for project_state()'s DB round trip -- one fetch call
    returns every seeded claim row, exactly as test_derive_offline.py's
    FakePool does (each derive_* function issues exactly one fetch)."""

    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *params):
        return self._rows


def _build_evidence(spec: dict) -> ProcedureEvidence:
    return ProcedureEvidence(
        goal_text=spec["goal_text"],
        outcome=spec["outcome"],
        observations=spec.get("observations", []),
        tool_sequence=spec.get("tool_sequence", []),
        started_at=datetime.now(timezone.utc),
        project_id=spec.get("project_id"),
    )


def _extract(case: dict):
    evidence = _build_evidence(case["evidence"])
    subject = f"project:{case['evidence']['project_id']}"
    rows = [
        _claim_row(c["id"], c["predicate"], c["object"], subject)
        for c in case.get("claim_rows", [])
    ]
    pool = FakePool(rows)
    return asyncio.run(
        DeterministicExtractor().extract(pool, evidence, repo_root=None, entry_seed_files=None)
    )


def _precondition_tuples(preconditions) -> list[tuple]:
    return [(p.predicate, p.object, getattr(p, "claim_id", None)) for p in preconditions]


def _slot_tuples(slots) -> list[tuple]:
    return [(s.name, s.binder, s.description) for s in slots]


def run_case(case: dict) -> dict:
    expected = case["expected"]
    extracted = _extract(case)

    actual_steps = [s.action for s in extracted.steps]
    expected_preconditions = [
        (e["predicate"], e["object"], e.get("claim_id")) for e in expected["preconditions"]
    ]
    expected_slots = [(s["name"], s["binder"], s["description"]) for s in expected["slots"]]

    mismatches = []
    if extracted.goal != expected["goal"]:
        mismatches.append(f"goal: {extracted.goal!r} != {expected['goal']!r}")
    if actual_steps != expected["steps"]:
        mismatches.append(f"steps: {actual_steps!r} != {expected['steps']!r}")
    if [s.order for s in extracted.steps] != list(range(1, len(extracted.steps) + 1)):
        mismatches.append(f"step order not 1..N: {[s.order for s in extracted.steps]!r}")
    actual_preconditions = _precondition_tuples(extracted.preconditions)
    if actual_preconditions != expected_preconditions:
        mismatches.append(f"preconditions: {actual_preconditions!r} != {expected_preconditions!r}")
    if extracted.scope != expected["scope"]:
        mismatches.append(f"scope: {extracted.scope!r} != {expected['scope']!r}")
    if extracted.failure_conditions != expected["failure_conditions"]:
        mismatches.append(
            f"failure_conditions: {extracted.failure_conditions!r} != {expected['failure_conditions']!r}"
        )
    actual_slots = _slot_tuples(extracted.slots)
    if actual_slots != expected_slots:
        mismatches.append(f"slots: {actual_slots!r} != {expected_slots!r}")

    step_precision, step_recall = metrics.step_precision_recall(actual_steps, expected["steps"])
    ordering = metrics.ordering_accuracy(actual_steps, expected["steps"])
    precondition_precision, precondition_recall = metrics.step_precision_recall(
        [str(t) for t in actual_preconditions], [str(t) for t in expected_preconditions]
    )
    # Provenance completeness: of the expected preconditions that carry a
    # real (non-null) gold claim_id, fraction where extraction produced
    # that exact claim_id -- silently losing provenance during extraction
    # is exactly the failure this metric exists to catch (spec section 14).
    gold_with_provenance = [e for e in expected_preconditions if e[2] is not None]
    if gold_with_provenance:
        provenance_hits = sum(1 for e in gold_with_provenance if e in actual_preconditions)
        provenance_completeness = provenance_hits / len(gold_with_provenance)
    else:
        provenance_completeness = 1.0  # nothing to lose

    return {
        "success": not mismatches,
        "failure_reason": "; ".join(mismatches) if mismatches else None,
        "metrics": {
            "goal_correct": extracted.goal == expected["goal"],
            "step_precision": step_precision,
            "step_recall": step_recall,
            "ordering_accuracy": ordering,
            "precondition_precision": precondition_precision,
            "precondition_recall": precondition_recall,
            "provenance_completeness": provenance_completeness,
            "scope_correct": extracted.scope == expected["scope"],
            "failure_conditions_correct": extracted.failure_conditions == expected["failure_conditions"],
        },
    }


def test_gold_procedures_against_deterministic_extractor():
    cases = load_gold_set(GOLD_PATH)
    results = run_gold_set("gold_procedures", cases, run_case)

    failures = [r for r in results if not r.success]
    assert not failures, "\n".join(
        f"{r.scenario_id}: {r.failure_reason}" for r in failures
    )

    # Aggregate metrics -- printed via the assertion message so a future
    # regression shows the real numbers, not just "assert False".
    n = len(results)
    avg = lambda key: sum(r.metrics[key] for r in results) / n  # noqa: E731
    aggregate = {
        "success_rate": success_rate(results),
        "goal_accuracy": sum(r.metrics["goal_correct"] for r in results) / n,
        "step_precision": avg("step_precision"),
        "step_recall": avg("step_recall"),
        "ordering_accuracy": avg("ordering_accuracy"),
        "precondition_precision": avg("precondition_precision"),
        "precondition_recall": avg("precondition_recall"),
        "provenance_completeness": avg("provenance_completeness"),
        "scope_accuracy": sum(r.metrics["scope_correct"] for r in results) / n,
        "failure_mode_accuracy": sum(r.metrics["failure_conditions_correct"] for r in results) / n,
    }
    assert aggregate["success_rate"] == 1.0, aggregate


def test_gold_set_has_no_duplicate_or_empty_case_ids():
    cases = load_gold_set(GOLD_PATH)
    ids = [c["id"] for c in cases]
    assert all(ids)
    assert len(ids) == len(set(ids))
    assert len(cases) >= 5


def test_incidental_action_gap_is_pinned_not_silently_fixed():
    """DeterministicExtractor does not filter incidental tool calls (it is
    an honest literal replay, by design -- see strategies.py). This test
    exists so that if a future change DOES start filtering, it shows up as
    a deliberate diff here rather than silently changing behavior no one
    asked for. True incidental-action filtering belongs to the LLM
    abstraction path (GroundedHybridExtractor), out of scope offline."""
    cases = {c["id"]: c for c in load_gold_set(GOLD_PATH)}
    case = cases["incidental-read-literal-replay"]
    extracted = _extract(case)
    actual_steps = [s.action for s in extracted.steps]
    assert actual_steps == ["Call Read", "Call Edit", "Call Bash"]
    assert len(actual_steps) == 3, (
        "if this now excludes the incidental Read, update case expected steps "
        "AND evaluation/README.md's Known limitations -- the gap this pins may be closed"
    )
