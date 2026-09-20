"""Offline tests for the semantic-extraction response parser
(trajectory-ingestion-hardening task, Sec 6). `parse_extraction_response`
is pure -- no database, no network, no LLM call."""
from __future__ import annotations

import json

import pytest

from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.trajectory_semantics import parse_extraction_response


def _valid_payload(**overrides) -> dict:
    payload = {
        "primary_goal": {
            "text": "fix the failing test in test_bar.py",
            "event_indices": [1, 2],
            "epistemic_status": "inferred",
            "confidence": 0.8,
        },
        "subgoals": [],
        "candidate_procedures": [],
        "claims": [],
        "preconditions": [],
        "failure_modes": [],
        "recovery_patterns": [],
        "verification_actions": [],
        "outcome": "success",
        "reusable_elements": [],
        "uncertainties": [],
    }
    payload.update(overrides)
    return payload


def test_valid_response_parses_cleanly():
    text = json.dumps(_valid_payload())
    result = parse_extraction_response(text, max_index=5)
    assert result.primary_goal.text == "fix the failing test in test_bar.py"
    assert result.outcome == "success"
    assert result.uncertainties == []


def test_non_json_response_raises_transient_failure():
    with pytest.raises(ExtractionTransientFailure):
        parse_extraction_response("not json at all", max_index=5)


def test_schema_violating_response_raises_transient_failure():
    """extra='forbid' + missing required fields must be caught, not
    silently coerced into something partially wrong."""
    text = json.dumps({"totally": "wrong shape"})
    with pytest.raises(ExtractionTransientFailure):
        parse_extraction_response(text, max_index=5)


def test_out_of_range_event_index_is_filtered_not_trusted():
    payload = _valid_payload(
        claims=[{
            "text": "the fix worked",
            "event_indices": [1, 99],  # 99 is out of range for max_index=3
            "epistemic_status": "observed",
            "confidence": 0.9,
        }],
    )
    result = parse_extraction_response(json.dumps(payload), max_index=3)
    assert result.claims[0].event_indices == [1]


def test_element_with_zero_valid_indices_is_dropped_into_uncertainties():
    payload = _valid_payload(
        claims=[{
            "text": "a claim citing nothing real",
            "event_indices": [50],
            "epistemic_status": "observed",
            "confidence": 0.9,
        }],
    )
    result = parse_extraction_response(json.dumps(payload), max_index=3)
    assert result.claims == []
    assert any("dropped" in u for u in result.uncertainties)


def test_candidate_procedure_with_no_valid_steps_is_dropped():
    payload = _valid_payload(
        candidate_procedures=[{
            "capability_statement": "fix and verify",
            "steps": [{"description": "run the fix", "event_indices": [77]}],
            "event_indices": [1],
            "epistemic_status": "inferred",
            "confidence": 0.5,
        }],
    )
    result = parse_extraction_response(json.dumps(payload), max_index=3)
    assert result.candidate_procedures == []


def test_candidate_procedure_with_valid_steps_survives():
    payload = _valid_payload(
        candidate_procedures=[{
            "capability_statement": "reproduce then fix a failing test",
            "steps": [
                {"description": "reproduce the failure", "subgoal_text": "reproduce failure", "event_indices": [1]},
                {"description": "run targeted verification", "subgoal_text": "verify fix", "event_indices": [2]},
            ],
            "event_indices": [1, 2],
            "epistemic_status": "inferred",
            "confidence": 0.6,
        }],
    )
    result = parse_extraction_response(json.dumps(payload), max_index=3)
    assert len(result.candidate_procedures) == 1
    assert len(result.candidate_procedures[0].steps) == 2
    assert result.candidate_procedures[0].steps[0].subgoal_text == "reproduce failure"


def test_empty_lists_are_a_legitimate_abstain_not_an_error():
    """A trajectory with nothing extractable is a real, valid outcome --
    an all-empty response must parse fine, never raise."""
    result = parse_extraction_response(json.dumps(_valid_payload(primary_goal=None)), max_index=3)
    assert result.primary_goal is None
    assert result.candidate_procedures == []
    assert result.claims == []


def test_failure_outcome_trajectory_still_parses_with_failure_content():
    """Task Sec 16: a failed trajectory must still produce useful
    knowledge -- failure_modes/recovery_patterns are real, populated
    fields, not forced empty just because outcome='failure'."""
    payload = _valid_payload(
        outcome="failure",
        primary_goal={
            "text": "fix the failing test",
            "event_indices": [1],
            "epistemic_status": "inferred",
            "confidence": 0.7,
        },
        failure_modes=[{
            "text": "repeated edits without additional diagnosis between attempts",
            "event_indices": [2, 4, 6],
            "epistemic_status": "inferred",
            "confidence": 0.6,
        }],
        recovery_patterns=[{
            "text": "agent re-ran the same test after each edit without changing approach",
            "event_indices": [2, 3, 4, 5],
            "epistemic_status": "inferred",
            "confidence": 0.5,
        }],
    )
    result = parse_extraction_response(json.dumps(payload), max_index=6)
    assert result.outcome == "failure"
    assert len(result.failure_modes) == 1
    assert len(result.recovery_patterns) == 1
