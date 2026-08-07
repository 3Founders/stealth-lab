"""
Tests for Rule 1 (app/services/precondition_gate.py), including the
exact DE-vs-SWE adversarial scenario the synthetic Experiment 2-B run
used -- this confirms the gate actually closes the gap that run found,
not just that the gate logic is internally consistent.
"""
from __future__ import annotations

from app.services.precondition_gate import extract_postconditions, postconditions_compatible


def test_no_postconditions_either_side_passes_trivially():
    """Zero regression on today's behavior when nothing supplies
    structured postconditions -- the common case right now."""
    assert postconditions_compatible(None, None) is True
    assert postconditions_compatible(["schema_valid"], None) is True
    assert postconditions_compatible(None, ["schema_valid"]) is True


def test_the_adversarial_case_from_the_synthetic_experiment_2b_run():
    """
    The exact scenario: shared surface instruction ('validate the
    output'), DE means schema-conformance, SWE means test-suite-
    passing. The synthetic run showed embedding similarity alone
    flips between wrongly-matching and correctly-rejecting depending
    entirely on incidental phrasing weight. The gate must reject this
    pair regardless of what the embedding says, whenever both sides
    actually state their postconditions.
    """
    de_postconditions = ["schema_conformance", "field_types_valid"]
    swe_postconditions = ["test_suite_passes", "no_regressions"]
    assert postconditions_compatible(de_postconditions, swe_postconditions) is False


def test_genuinely_compatible_postconditions_pass():
    a = ["schema_conformance", "field_types_valid", "no_null_ids"]
    b = ["schema_conformance", "field_types_valid"]
    assert postconditions_compatible(a, b) is True


def test_partial_overlap_respects_threshold():
    a = ["a", "b", "c", "d"]
    b = ["a", "e", "f", "g"]  # 1 shared out of 7 union -> ~0.14, below default 0.25
    assert postconditions_compatible(a, b, threshold=0.25) is False
    assert postconditions_compatible(a, b, threshold=0.10) is True


def test_extract_postconditions_reads_the_optional_jsonb_field():
    assert extract_postconditions({"postconditions": ["schema_valid"]}) == ["schema_valid"]
    assert extract_postconditions({"other_key": "value"}) is None
    assert extract_postconditions(None) is None
    assert extract_postconditions({}) is None
    assert extract_postconditions({"postconditions": "not_a_list"}) is None


def test_tag_normalization_is_case_and_whitespace_insensitive():
    assert postconditions_compatible(["Schema_Conformance "], [" schema_conformance"]) is True
