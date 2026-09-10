"""
MCP hardening B34: pure-logic half of the verification ladder --
`derive_criteria` needs no database.
"""
from app.services.verification import METHODS, STATES, Criterion, derive_criteria


def test_methods_and_states_match_migration_55s_check_constraints():
    assert METHODS == (
        "self_report", "artifact_inspection", "deterministic_check",
        "independent_agent", "human_review", "real_world_outcome",
    )
    assert STATES == (
        "claimed_done", "checked", "verified", "independently_verified",
        "failed_verification", "inconclusive",
    )


def test_derive_criteria_from_plain_string_postconditions():
    procedure = {"postconditions": ["the tests pass", "the diff is non-empty"]}
    criteria = derive_criteria(procedure)
    assert criteria == [
        Criterion(criterion_id="postcondition:0", statement="the tests pass", required=True),
        Criterion(criterion_id="postcondition:1", statement="the diff is non-empty", required=True),
    ]


def test_derive_criteria_from_dict_postconditions_with_required_false():
    procedure = {"postconditions": [{"statement": "nice to have", "required": False}]}
    criteria = derive_criteria(procedure)
    assert criteria == [Criterion(criterion_id="postcondition:0", statement="nice to have", required=False)]


def test_derive_criteria_skips_malformed_entries_without_fabricating():
    procedure = {"postconditions": ["real one", {}, {"required": True}, 42, None]}
    criteria = derive_criteria(procedure)
    assert [c.criterion_id for c in criteria] == ["postcondition:0"]


def test_derive_criteria_on_empty_or_missing_postconditions():
    assert derive_criteria({"postconditions": []}) == []
    assert derive_criteria({}) == []
