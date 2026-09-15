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


# ---------------------------------------------------------------------
# Meta-harness Sec 14: node-scoped criteria from step["verification"]
# ---------------------------------------------------------------------


def test_derive_criteria_from_plain_string_step_verification():
    procedure = {"steps": [{"order": 0, "goal": "regen", "verification": "generated-drift passes"}]}
    criteria = derive_criteria(procedure)
    assert criteria == [
        Criterion(criterion_id="step:0:verification", statement="generated-drift passes", required=True),
    ]
    assert criteria[0].execution_run_node_id is None  # no mapping given


def test_derive_criteria_tags_step_criterion_with_real_node_id_when_mapping_given():
    procedure = {"steps": [
        {"order": 0, "goal": "regen", "verification": "generated-drift passes"},
        {"order": 1, "goal": "deploy", "verification": {"statement": "smoke test passes", "required": False}},
    ]}
    criteria = derive_criteria(procedure, node_id_by_step_order={0: "node-A", 1: "node-B"})
    by_id = {c.criterion_id: c for c in criteria}
    assert by_id["step:0:verification"].execution_run_node_id == "node-A"
    assert by_id["step:1:verification"].execution_run_node_id == "node-B"
    assert by_id["step:1:verification"].required is False


def test_derive_criteria_step_with_no_mapping_entry_stays_unscoped():
    procedure = {"steps": [{"order": 5, "goal": "x", "verification": "checked"}]}
    criteria = derive_criteria(procedure, node_id_by_step_order={0: "node-A"})
    assert criteria[0].execution_run_node_id is None


def test_derive_criteria_step_with_no_verification_contributes_nothing():
    procedure = {"steps": [{"order": 0, "goal": "just a goal, no verification"}]}
    assert derive_criteria(procedure) == []


def test_derive_criteria_combines_postconditions_and_step_verification():
    procedure = {
        "postconditions": ["overall thing works"],
        "steps": [{"order": 0, "goal": "regen", "verification": "generated-drift passes"}],
    }
    criteria = derive_criteria(procedure, node_id_by_step_order={0: "node-A"})
    assert [c.criterion_id for c in criteria] == ["postcondition:0", "step:0:verification"]
    assert criteria[0].execution_run_node_id is None
    assert criteria[1].execution_run_node_id == "node-A"
