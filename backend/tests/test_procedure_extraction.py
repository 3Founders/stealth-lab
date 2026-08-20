"""
DB-free unit tests for procedure_extraction's pure/near-pure pieces:
derive.py's step/failure-condition derivation, slot_binders.py's
coverage logic, and schema.py's contract. Live-DB tests for
derive_preconditions/derive_scope live in
test_procedure_extraction_e2e.py (they need real project_state()).
"""
import pytest
from pydantic import ValidationError

from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_slots,
    derive_step_skeleton,
    literal_steps_from_skeleton,
)
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import ExtractedProcedure, Predicate, ProcedureStep, SlotSpec
from app.services.procedure_extraction.strategies import _parse_abstraction_response
from app.services.procedure_extraction.validators import ValidationContext, validate
from app.services.slot_binders import best_binder_for, known_binder_names


def test_step_skeleton_run_length_encodes_consecutive_tool_calls():
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        tool_sequence=["Read", "Read", "Read", "Edit", "Bash", "Bash"],
    )
    groups = derive_step_skeleton(ev)
    assert [(g.tool_name, g.count) for g in groups] == [("Read", 3), ("Edit", 1), ("Bash", 2)]


def test_step_skeleton_empty_tool_sequence_produces_no_groups():
    ev = ProcedureEvidence(goal_text="g", outcome="success", tool_sequence=[])
    assert derive_step_skeleton(ev) == []


def test_literal_steps_are_never_deps_or_requires_bearing():
    """Structural guarantee, not a runtime check: ProcedureStep has no
    deps/requires field at all -- ticket 05's planner-neutral rule,
    enforced by the type itself."""
    ev = ProcedureEvidence(goal_text="g", outcome="success", tool_sequence=["Edit"])
    steps = literal_steps_from_skeleton(derive_step_skeleton(ev))
    assert steps
    for s in steps:
        assert not hasattr(s, "deps")
        assert not hasattr(s, "requires")


def test_failure_conditions_only_from_real_recorded_failures():
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[
            {"observation_type": "test_run", "properties": {"passed": True}},
            {"observation_type": "command_executed", "properties": {"command": "ls", "exit_code": 0}},
        ],
    )
    assert derive_failure_conditions(ev) == [], "no real failure recorded -- must not fabricate one"


def test_failure_conditions_deduplicated():
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[
            {"observation_type": "test_run", "properties": {"passed": False}},
            {"observation_type": "test_run", "properties": {"passed": False}},
        ],
    )
    conditions = derive_failure_conditions(ev)
    assert len(conditions) == 1


def test_derive_slots_falls_back_to_literal_without_repo_root():
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[{"observation_type": "file_touched", "properties": {"file_path": "a.py"}}],
    )
    slots = derive_slots(ev, repo_root=None, entry_seed_files=[])
    assert len(slots) == 1
    assert slots[0].binder == "literal"


def test_derive_slots_empty_without_any_file_touched_observations():
    ev = ProcedureEvidence(goal_text="g", outcome="success", observations=[])
    assert derive_slots(ev, repo_root=".", entry_seed_files=["x.py"]) == []


def test_derive_slots_uses_a_real_registered_binder_against_this_repos_own_files():
    """Real regression against this repo's own real import graph, same
    discipline import_deps.py's own tests use."""
    import os
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[
            {"observation_type": "file_touched",
             "properties": {"file_path": "app/services/import_deps.py"}},
        ],
    )
    slots = derive_slots(
        ev, repo_root=backend_root, entry_seed_files=["app/services/related_tests.py"],
    )
    assert len(slots) == 1
    assert slots[0].binder in known_binder_names()


def test_best_binder_for_prefers_call_graph_over_literal_when_it_covers_more():
    import os
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # A file that genuinely calls into other real files in this repo --
    # local_retrieval.py imports app.services.access and app.services.retrieval.
    result = best_binder_for(
        backend_root,
        ["app/services/local_retrieval.py"],
        {"app/services/access.py"},
    )
    assert result != "literal"


def test_best_binder_for_falls_back_to_literal_with_no_coverage():
    import os
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result = best_binder_for(
        backend_root, ["app/services/local_retrieval.py"], {"totally/unrelated/nonexistent.xyz"},
    )
    assert result == "literal"


# --- schema.py contract ---

def test_extracted_procedure_rejects_zero_steps():
    with pytest.raises(ValidationError):
        ExtractedProcedure(
            name="n", goal="g", capability_statement="c", steps=[],
        )


def test_extracted_procedure_rejects_empty_step_action():
    with pytest.raises(ValidationError):
        ProcedureStep(order=1, action="   ")


def test_procedure_step_has_no_deps_requires_fields():
    """Mirrors the earlier structural test but pinned directly against
    the Pydantic model's own field set, so a future edit that ADDS
    deps/requires back onto ProcedureStep fails this test immediately."""
    assert "deps" not in ProcedureStep.model_fields
    assert "requires" not in ProcedureStep.model_fields


def test_predicate_round_trips_a_real_project_state_shape():
    p = Predicate(subject="project:x", predicate="has_test_runner", object="pytest")
    assert p.model_dump() == {"subject": "project:x", "predicate": "has_test_runner", "object": "pytest"}


# --- validators.py: the fail-closed trap and friends ---

def _ctx(**overrides) -> ValidationContext:
    defaults = dict(
        probe_vocabulary=("has_test_runner", "language"),
        evidence_tokens=frozenset(),
        allowed_binders=frozenset(known_binder_names()),
    )
    defaults.update(overrides)
    return ValidationContext(**defaults)


def test_v1_rejects_a_precondition_outside_the_probe_vocabulary():
    """THE fail-closed trap, tested explicitly: a precondition naming a
    predicate nothing asserts is permanently unmatchable, silently,
    without this rule."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
        preconditions=[Predicate(subject="project:x", predicate="invented_predicate", object="y")],
    )
    failures = validate(proc, _ctx())
    assert any(f.rule == "V1_precondition_groundedness" for f in failures)


def test_v1_accepts_a_precondition_inside_the_vocabulary():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
        preconditions=[Predicate(subject="project:x", predicate="has_test_runner", object="pytest")],
    )
    failures = validate(proc, _ctx())
    assert not any(f.rule == "V1_precondition_groundedness" for f in failures)


def test_v3_rejects_a_step_referencing_an_undeclared_slot():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="edit {undeclared}")],
    )
    failures = validate(proc, _ctx())
    assert any(f.rule == "V3_slot_integrity" and "undeclared" in f.message for f in failures)


def test_v3_rejects_a_slot_with_an_unknown_binder():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="edit {target}")],
        slots=[SlotSpec(name="target", binder="not_a_real_binder")],
    )
    failures = validate(proc, _ctx())
    assert any(f.rule == "V3_slot_integrity" and "not_a_real_binder" in f.message for f in failures)


def test_v3_rejects_a_known_binder_not_in_allowed_binders():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="edit {target}")],
        slots=[SlotSpec(name="target", binder="call_graph_reachable")],
    )
    failures = validate(proc, _ctx(allowed_binders=frozenset({"literal"})))
    assert any(f.rule == "V3_slot_integrity" for f in failures)


def test_v4_rejects_a_capability_statement_containing_an_evidence_path():
    proc = ExtractedProcedure(
        name="n", goal="g",
        capability_statement="edit app/services/foo.py to fix the bug",
        steps=[ProcedureStep(order=1, action="do it")],
    )
    failures = validate(proc, _ctx(evidence_tokens=frozenset({"app/services/foo.py"})))
    assert any(f.rule == "V4_capability_abstraction" for f in failures)


def test_v4_accepts_a_genuinely_abstract_statement():
    proc = ExtractedProcedure(
        name="n", goal="g",
        capability_statement="locate the failing symbol via call graph and apply a minimal fix",
        steps=[ProcedureStep(order=1, action="do it")],
    )
    failures = validate(proc, _ctx(evidence_tokens=frozenset({"app/services/foo.py"})))
    assert not any(f.rule == "V4_capability_abstraction" for f in failures)


def test_all_rules_run_failures_are_not_short_circuited():
    """Unlike applicability.py's deliberate short-circuit, every rule
    must run and every failure must be collected -- two independent bad
    things in one procedure must both be reported."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="edit {missing}")],
        preconditions=[Predicate(subject="project:x", predicate="invented", object="y")],
    )
    failures = validate(proc, _ctx())
    rules_hit = {f.rule for f in failures}
    assert "V1_precondition_groundedness" in rules_hit
    assert "V3_slot_integrity" in rules_hit


# --- strategies.py: _parse_abstraction_response, pure, no client needed ---

def test_parse_abstraction_response_happy_path():
    text = "CAPABILITY: locate the failing symbol and apply a minimal fix\nSTEPS: find the relevant files; apply a targeted edit; run the tests"
    result = _parse_abstraction_response(text, expected_step_count=3)
    assert result is not None
    capability, steps = result
    assert capability == "locate the failing symbol and apply a minimal fix"
    assert steps == ["find the relevant files", "apply a targeted edit", "run the tests"]


def test_parse_abstraction_response_explicit_abstain_returns_none():
    assert _parse_abstraction_response("ABSTAIN", expected_step_count=2) is None


def test_parse_abstraction_response_wrong_step_count_returns_none():
    """The model inventing or dropping steps relative to what actually
    happened must trigger the fallback, not silently be accepted."""
    text = "CAPABILITY: x\nSTEPS: a; b"
    assert _parse_abstraction_response(text, expected_step_count=3) is None


def test_parse_abstraction_response_missing_label_returns_none():
    assert _parse_abstraction_response("just some text with no labels", expected_step_count=1) is None
