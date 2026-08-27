"""
DB-free coverage for backend/scripts/bootstrap_demo.py's own NEW decision
logic (select_target_precondition, verdict_cites_both_claims) -- the two
pure functions Phase B's real-DB flow relies on. Everything else the
script calls (extract_procedure, check_procedure_reuse, relate_claims,
project_state, find_applicable_procedures, assert_environment_claims) is
already proven by its own module's offline/e2e suite; this file does not
re-prove those.

bootstrap_demo.py lives under backend/scripts/, not backend/app/, so it
is loaded here via importlib rather than a normal package import -- same
reasoning any of this repo's other top-level scripts would need if they
grew tests. This module has no side effects at import time (`if __name__
== "__main__"` guards the only I/O-triggering call), so importing it here
is safe and does not touch a database or the network.
"""
import importlib.util
import os

import pytest

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "bootstrap_demo.py",
)
_spec = importlib.util.spec_from_file_location("bootstrap_demo", _SCRIPT_PATH)
bootstrap_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap_demo)


# --------------------------------------------------- select_target_precondition

def test_prefers_has_test_runner_when_present():
    preconditions = [
        {"subject": "project:p", "predicate": "language", "object": "python"},
        {"subject": "project:p", "predicate": "has_test_runner", "object": "pytest"},
        {"subject": "project:p", "predicate": "package_manager", "object": "pip"},
    ]
    target = bootstrap_demo.select_target_precondition(preconditions)
    assert target["predicate"] == "has_test_runner"


def test_falls_back_to_the_first_precondition_when_no_test_runner_claim_exists():
    preconditions = [
        {"subject": "project:p", "predicate": "language", "object": "python"},
        {"subject": "project:p", "predicate": "package_manager", "object": "pip"},
    ]
    target = bootstrap_demo.select_target_precondition(preconditions)
    assert target["predicate"] == "language"


def test_single_precondition_is_returned_regardless_of_predicate():
    preconditions = [{"subject": "project:p", "predicate": "language", "object": "python"}]
    target = bootstrap_demo.select_target_precondition(preconditions)
    assert target == preconditions[0]


def test_raises_on_empty_preconditions_rather_than_silently_returning_nothing():
    with pytest.raises(ValueError, match="at least one precondition"):
        bootstrap_demo.select_target_precondition([])


# ----------------------------------------------------- verdict_cites_both_claims

CLAIM_A = "00000000-0000-4000-8000-0000000000c1"
CLAIM_B = "00000000-0000-4000-8000-0000000000c2"


def test_true_when_evidence_cites_both_claim_ids_demo_md_style():
    evidence = [f"claim:{CLAIM_A}", f"claim:{CLAIM_B}"]
    assert bootstrap_demo.verdict_cites_both_claims(evidence, CLAIM_A, CLAIM_B) is True


def test_false_when_only_one_claim_is_cited():
    evidence = [f"claim:{CLAIM_A}"]
    assert bootstrap_demo.verdict_cites_both_claims(evidence, CLAIM_A, CLAIM_B) is False


def test_false_when_evidence_cites_neither_claim():
    evidence = [f"procedure:{CLAIM_A}"]
    assert bootstrap_demo.verdict_cites_both_claims(evidence, CLAIM_A, CLAIM_B) is False


def test_false_on_empty_evidence():
    assert bootstrap_demo.verdict_cites_both_claims([], CLAIM_A, CLAIM_B) is False


# --------------------------------------------------------------- module shape

def test_observations_are_load_bearing_for_language_and_has_test_runner():
    """Guards the exact real signature derive.py's load_bearing_predicates
    checks for -- if this drifts (e.g. someone edits OBSERVATIONS without
    checking derive.py), Phase A would silently produce zero
    preconditions instead of failing loudly, which is exactly the
    failure mode this script's own Phase A guard exists to catch at
    runtime. Proven here without a DB, since load_bearing_predicates is
    itself pure."""
    from app.services.procedure_extraction.derive import load_bearing_predicates
    from app.services.procedure_extraction.evidence import ProcedureEvidence

    evidence = ProcedureEvidence(
        goal_text=bootstrap_demo.GOAL_TEXT, outcome="success",
        observations=bootstrap_demo.OBSERVATIONS, tool_sequence=bootstrap_demo.TOOL_SEQUENCE,
    )
    predicates = load_bearing_predicates(evidence)
    assert "language" in predicates
    assert "has_test_runner" in predicates


def test_phase_functions_are_coroutine_functions():
    import inspect
    assert inspect.iscoroutinefunction(bootstrap_demo.phase_a)
    assert inspect.iscoroutinefunction(bootstrap_demo.phase_b)
    assert inspect.iscoroutinefunction(bootstrap_demo.main)
