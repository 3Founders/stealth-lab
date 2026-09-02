"""Gold-set evaluation for historical evidence classification (spec section
6), focused on the ChatGPT edited/regenerated-branch case specifically.

app.local_agent.chat_history_import's own module docstring documents a
deliberate scope limit: parse_chatgpt_export does not walk the export's
parent/children edit-branch structure -- it takes every node with a
non-empty message and sorts by create_time. This is honest (documented),
not silently wrong, but it is a real conservatism risk the pre-work audit
flagged and no existing test exercised: a regenerated/abandoned sibling
branch can still be linearized into the conversation and influence
evidence classification, in BOTH directions --

  1. a fabricated/abandoned 'tool' result can leak in as real evidence and
     inflate the whole conversation's evidence_level (false positive), or
  2. a hedged, abandoned sibling can poison P0-3's "last command was
     hedged" bookkeeping and wrongly suppress a genuinely earned later
     confirmation (false negative).

Both are proven here against the REAL parse_chatgpt_export +
extract_candidates_from_conversation functions -- this file characterizes
actual behavior, it does not invent new production code or weaken an
assertion to force a pass. A control case (no branching) confirms the
harness plumbing and the classifier pipeline are correct absent the
adversarial condition.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.local_agent.chat_history_import import (
    extract_candidates_from_conversation,
    parse_chatgpt_export,
)
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set, success_rate

GOLD_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gold_evidence" / "cases.json"


def _parse_export(export_data: list[dict]):
    # parse_chatgpt_export reads from a real path (json.load(open(path))) --
    # no in-memory entrypoint exists, so a real temp file is the honest way
    # to exercise it rather than reaching past the module's own API.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export_data, f)
        tmp_path = f.name
    try:
        return parse_chatgpt_export(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run_case(case: dict) -> dict:
    expected = case["expected"]
    conversations = _parse_export(case["export"])
    assert len(conversations) == 1, "gold fixtures are one conversation per case"
    conv = conversations[0]

    candidates = extract_candidates_from_conversation(conv)
    produced = bool(candidates)
    max_level = candidates[0]["evidence_refs"][0]["evidence_level"] if candidates else None

    mismatches = []
    if produced != expected["produces_candidate"]:
        mismatches.append(
            f"produces_candidate: {produced!r} != {expected['produces_candidate']!r}"
        )
    if max_level != expected["max_evidence_level"]:
        mismatches.append(
            f"max_evidence_level: {max_level!r} != {expected['max_evidence_level']!r}"
        )

    return {
        "success": not mismatches,
        "failure_reason": "; ".join(mismatches) if mismatches else None,
        "metrics": {
            "produces_candidate": produced,
            "max_evidence_level": max_level,
            "message_count": len(conv.messages),
        },
    }


def test_gold_evidence_chatgpt_branch_cases_match_documented_real_behavior():
    """This test PASSING means the gold labels correctly characterize what
    the real code does today -- including the two documented gaps (leaked
    verified-evidence from an abandoned tool branch; suppressed valid
    confirmation from a hedged abandoned branch). It is a regression pin,
    not a claim that this behavior is safe or desired -- see
    evaluation-results/final-scorecard.md and evaluation/README.md's Known
    limitations for the reportable finding this test exists to catch drift
    on."""
    cases = load_gold_set(GOLD_PATH)
    results = run_gold_set("gold_evidence", cases, run_case)

    failures = [r for r in results if not r.success]
    assert not failures, "\n".join(f"{r.scenario_id}: {r.failure_reason}" for r in failures)
    assert success_rate(results) == 1.0


def test_leaked_tool_result_case_is_the_reportable_false_positive():
    """Isolates the sharper of the two findings with its own explicit
    assertion (not just gold-set equality) so its significance can't get
    lost in an aggregate pass/fail: an abandoned branch alone can drive a
    real candidate to evidence_level 'verified'."""
    cases = {c["id"]: c for c in load_gold_set(GOLD_PATH)}
    case = cases["regenerated-tool-result-leaks-as-verified-evidence"]
    conv = _parse_export(case["export"])[0]
    candidates = extract_candidates_from_conversation(conv)
    assert len(candidates) == 1
    assert candidates[0]["evidence_refs"][0]["evidence_level"] == "verified", (
        "if this changes, the regenerated-branch leak has been fixed -- update this pin "
        "AND evaluation/README.md's Known limitations, don't just relax the assertion"
    )


def test_gold_set_has_no_duplicate_or_empty_case_ids():
    cases = load_gold_set(GOLD_PATH)
    ids = [c["id"] for c in cases]
    assert all(ids)
    assert len(ids) == len(set(ids))
    assert len(cases) >= 2
