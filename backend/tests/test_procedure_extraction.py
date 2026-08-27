"""
DB-free unit tests for procedure_extraction's pure/near-pure pieces:
derive.py's step/failure-condition derivation AND its load-bearing
relevance filter (ticket 1.8a -- pure functions, proven offline),
validators.py's rules including V6 (ticket 1.8b), slot_binders.py's
coverage logic, and schema.py's contract. FakePool coverage of
derive_preconditions/derive_scope's real project_state() round trip
lives in test_derive_offline.py; live-DB proof against a real Postgres
instance is test_procedure_extraction_e2e.py.
"""
import pytest
from pydantic import ValidationError

from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_slots,
    derive_step_skeleton,
    filter_load_bearing_claims,
    literal_steps_from_skeleton,
    load_bearing_predicates,
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


# --- section-13 step fields: tool binding survives extraction ---

def test_literal_steps_carry_tool_name_structurally():
    """The regression this whole change exists to prevent: StepGroup has
    always known the tool name, and literal_steps_from_skeleton used to
    discard it into prose, so every real agent run lost its tool-call
    shape at extraction time."""
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        tool_sequence=["Read", "Read", "Edit"],
    )
    steps = literal_steps_from_skeleton(derive_step_skeleton(ev))
    assert [s.allowed_implementations for s in steps] == [
        [{"type": "tool", "name": "Read"}],
        [{"type": "tool", "name": "Edit"}],
    ]
    # `action` must be byte-identical to the old behaviour -- it is what
    # existing readers consume.
    assert [s.action for s in steps] == ["Call Read (2x)", "Call Edit"]


def test_new_step_fields_default_without_being_supplied():
    """Every section-13 field is optional: the two-kwarg construction
    used by every pre-existing call site and test must keep working."""
    step = ProcedureStep(order=1, action="do the thing")
    assert step.goal is None
    assert step.inputs == []
    assert step.preconditions == []
    assert step.allowed_implementations == []
    assert step.expected_outputs == []
    assert step.verification is None
    assert step.failure_policy is None
    assert step.cost_budget is None


def test_v3_scans_slot_placeholders_outside_action():
    """A {slot} in goal/inputs is as real as one in action -- scanning
    only `action` would let an undeclared slot escape silently."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="abstract capability",
        steps=[ProcedureStep(order=1, action="do it", goal="edit {undeclared_slot}")],
    )
    failures = validate(proc, _ctx())
    assert any(
        f.rule == "V3_slot_integrity" and "undeclared_slot" in f.message
        for f in failures
    )


# --- ticket 1.8a: load-bearing relevance filter (derive.py, pure) ---

def _claim(predicate, obj="x", subject="project:p"):
    return {"id": f"{subject}:{predicate}", "subject": subject,
            "predicate": predicate, "object": obj}


def _evidence_with(observations):
    return ProcedureEvidence(goal_text="g", outcome="success", observations=observations)


def test_no_behavioral_evidence_means_no_gates_at_all():
    """The 1.8a tightening itself: an episode whose observations show
    nothing was exercised gates on NOTHING -- every live claim stays out
    rather than becoming a permanent CWA rejection trigger."""
    claims = [_claim("has_test_runner", "pytest"), _claim("language", "python")]
    assert filter_load_bearing_claims(claims, _evidence_with([])) == []
    assert load_bearing_predicates(_evidence_with([])) == frozenset()


def test_running_the_test_suite_makes_has_test_runner_load_bearing():
    claims = [_claim("has_test_runner", "pytest"), _claim("has_dev_server", "vite")]
    kept = filter_load_bearing_claims(
        claims,
        _evidence_with([{"observation_type": "test_run", "properties": {"passed": True}}]),
    )
    assert [(c["predicate"], c["object"]) for c in kept] == [("has_test_runner", "pytest")]


def test_command_content_promotes_the_facet_it_exercises():
    ev = _evidence_with([
        {"observation_type": "command_executed",
         "properties": {"command": "pip install -r requirements.txt"}},
        {"observation_type": "command_executed",
         "properties": {"command": "python -m pytest tests -q"}},
    ])
    predicates = load_bearing_predicates(ev)
    assert "package_manager" in predicates
    assert "has_test_runner" in predicates


def test_build_and_dev_server_commands_map_to_their_own_predicates():
    ev = _evidence_with([
        {"observation_type": "command_executed",
         "properties": {"command": "npm run build && npx vite --port 5173"}},
    ])
    predicates = load_bearing_predicates(ev)
    assert "has_build_tool" in predicates
    assert "has_dev_server" in predicates


def test_touching_source_files_makes_language_load_bearing_but_manifests_do_not():
    source = _evidence_with([
        {"observation_type": "file_touched", "properties": {"file_path": "app/services/state.py"}},
    ])
    assert "language" in load_bearing_predicates(source)

    manifest = _evidence_with([
        {"observation_type": "file_touched", "properties": {"file_path": "package.json"}},
    ])
    assert "language" not in load_bearing_predicates(manifest), (
        "editing a manifest says nothing about needing a language runtime -- "
        "gating `language` on it would be a guess, and guesses fail closed forever"
    )


def test_has_framework_is_never_derived_until_a_real_signal_exists():
    """The documented honest gap: framework usage has no deterministic
    signature today, so a live has_framework claim must NOT become a
    gate no matter what else the episode did."""
    claims = [_claim("has_framework", "react"), _claim("package_manager", "npm")]
    ev = _evidence_with([
        {"observation_type": "command_executed",
         "properties": {"command": "npm run build"}},
        {"observation_type": "command_executed",
         "properties": {"command": "npx jest"}},
    ])
    kept = filter_load_bearing_claims(claims, ev)
    assert [c["predicate"] for c in kept] == ["package_manager"]


def test_filter_respects_the_vocabulary_drift_guard():
    """load_bearing_predicates only ever emits probe vocabulary names
    TODAY; the explicit vocabulary argument keeps that true by
    construction even if a future signal names something the probe
    stopped asserting -- derivation can never emit a gate validation
    (V1) would have to reject."""
    claims = [_claim("has_test_runner", "pytest")]
    ev = _evidence_with([
        {"observation_type": "command_executed", "properties": {"command": "pytest"}},
    ])
    assert filter_load_bearing_claims(claims, ev) == claims
    assert filter_load_bearing_claims(
        claims, ev, vocabulary=("language",),
    ) == [], "a signal outside the caller's stated vocabulary must not gate"


def test_filter_dedupes_identical_triples_order_preserving():
    claims = [
        _claim("has_test_runner", "pytest"),
        _claim("language", "python"),
        _claim("has_test_runner", "pytest"),   # duplicate
        _claim("language", "python"),          # duplicate
    ]
    ev = _evidence_with([
        {"observation_type": "command_executed", "properties": {"command": "pytest"}},
        {"observation_type": "file_touched", "properties": {"file_path": "main.py"}},
    ])
    kept = filter_load_bearing_claims(claims, ev)
    assert [(c["predicate"], c["object"]) for c in kept] == [
        ("has_test_runner", "pytest"), ("language", "python"),
    ]


def test_derive_preconditions_returns_empty_before_any_db_access_without_ids():
    """The early return must fire before the pool is touched -- provable
    offline by passing None as the pool."""
    import asyncio

    from app.services.procedure_extraction.derive import derive_preconditions

    evidence = ProcedureEvidence(goal_text="g", outcome="success",
                                 project_id=None, started_at=None)
    assert asyncio.run(derive_preconditions(None, evidence)) == []


# --- ticket 1.8b: V6 authoring-time invariant validator ---

def test_extracted_procedure_invariants_default_empty():
    """No evidence of a numeric constraint -> no invented invariant;
    every pre-existing construction site keeps working unchanged."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
    )
    assert proc.invariants == []


def test_v6_accepts_a_satisfiable_invariant():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
        invariants=[{"kind": "numeric", "expr": "amount <= balance"}],
    )
    failures = validate(proc, _ctx())
    assert not any(f.rule == "V6_invariant_authoring" for f in failures)


def test_v6_rejects_an_invariant_no_binding_can_ever_satisfy():
    """The numeric twin of V1's permanently-unmatchable precondition:
    parses fine, runs fine at check time -- and rejects every possible
    world. Must be caught at AUTHORING time, before persistence."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
        invariants=[{"kind": "numeric",
                     "expr": "amount <= balance and amount > balance"}],
    )
    failures = validate(proc, _ctx())
    v6 = [f for f in failures if f.rule == "V6_invariant_authoring"]
    assert v6 and "unsatisfiable" in v6[0].message


def test_v6_rejects_an_expression_runtime_would_error_on():
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="do it")],
        invariants=[{"kind": "numeric", "expr": "amount <="}],
    )
    failures = validate(proc, _ctx())
    v6 = [f for f in failures if f.rule == "V6_invariant_authoring"]
    assert v6 and "could not be parsed" in v6[0].message


def test_all_rules_run_collects_v6_alongside_others():
    """V6 joined the collect-everything chain: its failures surface in
    the same pass as every other rule's, never one retry at a time."""
    proc = ExtractedProcedure(
        name="n", goal="g", capability_statement="c",
        steps=[ProcedureStep(order=1, action="edit {missing}")],
        invariants=[{"kind": "numeric", "expr": "x < x"}],
    )
    rules_hit = {f.rule for f in validate(proc, _ctx())}
    assert "V3_slot_integrity" in rules_hit
    assert "V6_invariant_authoring" in rules_hit
