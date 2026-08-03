"""
One test per typechecker rule, each with a plan that violates exactly that
rule, plus a valid plan that passes cleanly.

The point of the "exactly that rule" discipline: a plan that trips three
rules at once proves nothing about which check caught it, and a rule can rot
into a no-op while its test still passes on a neighbour's finding.
"""
from __future__ import annotations

from uuid import uuid4


from app.models.plan import Expansion, Plan, PlanEdge, PlanNode
from app.services.typecheck import (
    TypecheckContext,
    schema_satisfies,
    topological_order,
    typecheck,
    typecheck_report,
)
from tests.helpers import node, obj, rules, valid_composite_plan, valid_plan


def test_valid_plan_passes_cleanly():
    assert typecheck(valid_plan()) == []


def test_valid_composite_plan_passes_cleanly():
    assert typecheck(valid_composite_plan()) == []


def test_empty_plan_is_rejected():
    assert rules(typecheck(Plan())) == {"well_formed"}


# --- well_formed -----------------------------------------------------------


def test_empty_schema_is_rejected():
    plan = valid_plan()
    plan.nodes[1].input_schema = {}
    problems = typecheck(plan)
    assert rules(problems) == {"well_formed"}
    assert "empty input_schema" in problems[0].message


def test_object_without_properties_counts_as_empty():
    """`{"type": "object"}` says nothing checkable -- it is declining to commit."""
    plan = valid_plan()
    plan.nodes[0].output_schema = {"type": "object"}
    assert rules(typecheck(plan)) == {"well_formed"}


def test_explicitly_empty_properties_is_allowed():
    """A node with genuinely no inputs is meaningful; the key is the commitment."""
    plan = Plan(
        nodes=[node("n1", {}, {"rates": "array"})],
        edges=[],
        external_inputs=[],
    )
    plan.nodes[0].input_schema = {"type": "object", "properties": {}}
    assert typecheck(plan) == []


def test_duplicate_refs_are_rejected():
    plan = valid_plan()
    plan.nodes[1].ref = "n1"
    problems = typecheck(plan)
    assert rules(problems) == {"well_formed"}
    assert any("duplicate node ref" in p.message for p in problems)


def test_self_edge_is_rejected():
    plan = valid_plan()
    plan.edges.append(PlanEdge(type="REQUIRES", source_ref="n1", target_ref="n1"))
    problems = typecheck(plan)
    assert rules(problems) == {"well_formed"}
    assert any("self-edge" in p.message for p in problems)


def test_edge_to_undeclared_ref_is_rejected():
    plan = valid_plan()
    plan.edges.append(PlanEdge(type="PRODUCES", source_ref="n2", target_ref="n99"))
    problems = typecheck(plan)
    assert rules(problems) == {"well_formed"}
    assert any("undeclared ref 'n99'" in p.message for p in problems)


# --- acyclicity ------------------------------------------------------------


def test_cycle_is_rejected():
    plan = valid_plan()
    # n2 hands pdf_path back to n1, which consumes it. Well-formed, closed,
    # type-compatible -- and a cycle.
    plan.nodes[1].output_schema = obj({"grid": "array", "pdf_path": "string"})
    plan.edges.append(PlanEdge(type="PRODUCES", source_ref="n2", target_ref="n1"))
    assert rules(typecheck(plan)) == {"acyclicity"}


def test_decomposes_to_is_not_a_cycle():
    """A composite pointing at its own children is structure, not a loop."""
    plan = valid_plan()
    plan.edges.append(PlanEdge(type="DECOMPOSES_TO", source_ref="n2", target_ref="n1"))
    assert typecheck(plan) == []


# --- dataflow_closure ------------------------------------------------------


def test_dangling_input_is_rejected():
    plan = valid_plan()
    plan.external_inputs = []  # pdf_path is now produced by nothing
    problems = typecheck(plan)
    assert rules(problems) == {"dataflow_closure"}
    assert all("pdf_path" in p.message for p in problems)


def test_input_satisfied_only_by_an_upstream_producer():
    plan = valid_plan()
    plan.external_inputs = ["pdf_path"]
    assert typecheck(plan) == []


# --- type_compatibility ----------------------------------------------------


def test_incompatible_types_across_an_edge_are_rejected():
    plan = valid_plan()
    plan.nodes[0].output_schema = obj({"regions": "string"})  # consumer wants an array
    problems = typecheck(plan)
    assert rules(problems) == {"type_compatibility"}
    assert "produces string" in problems[0].message


def test_integer_satisfies_number():
    plan = Plan(
        nodes=[
            node("n1", {"seed": "string"}, {"count": "integer"}),
            node("n2", {"count": "number"}, {"total": "number"}),
        ],
        edges=[PlanEdge(type="PRODUCES", source_ref="n1", target_ref="n2")],
        external_inputs=["seed"],
    )
    assert typecheck(plan) == []


def test_number_does_not_satisfy_integer():
    plan = Plan(
        nodes=[
            node("n1", {"seed": "string"}, {"count": "number"}),
            node("n2", {"count": "integer"}, {"total": "number"}),
        ],
        edges=[PlanEdge(type="PRODUCES", source_ref="n1", target_ref="n2")],
        external_inputs=["seed"],
    )
    assert rules(typecheck(plan)) == {"type_compatibility"}


def test_produces_edge_that_hands_over_nothing_is_rejected():
    plan = valid_plan()
    # n1 no longer produces anything n2 consumes, so the claimed dataflow is
    # a fiction -- but regions is still needed, so closure fires too.
    plan.nodes[0].output_schema = obj({"unrelated": "string"})
    problems = typecheck(plan)
    assert "type_compatibility" in rules(problems)
    assert any("shares no property" in p.message for p in problems)


def test_nested_object_mismatch_is_caught():
    produced = {"type": "object", "properties": {"page": {"type": "string"}}}
    required = {"type": "object", "properties": {"page": {"type": "integer"}},
                "required": ["page"]}
    assert schema_satisfies(produced, required, "region")


def test_missing_nested_property_is_caught():
    produced = {"type": "object", "properties": {"page": {"type": "integer"}}}
    required = {"type": "object", "properties": {"page": {"type": "integer"},
                                                 "bbox": {"type": "array"}},
                "required": ["page", "bbox"]}
    problems = schema_satisfies(produced, required, "region")
    assert any("bbox" in p for p in problems)


def test_opaque_object_is_not_treated_as_missing_everything():
    """An object that never enumerated its shape is unknown, not empty."""
    produced = {"type": "object"}
    required = {"type": "object", "properties": {"page": {"type": "integer"}},
                "required": ["page"]}
    assert schema_satisfies(produced, required, "region") == []


# --- executable_leaf -------------------------------------------------------


def test_leaf_without_an_implementation_is_rejected():
    plan = valid_plan()
    plan.nodes[1].implementations = []
    problems = typecheck(plan)
    assert rules(problems) == {"executable_leaf"}
    assert "nothing can run it" in problems[0].message


def test_disabled_implementation_does_not_count():
    plan = valid_plan()
    plan.nodes[1].implementations[0].enabled = False
    assert rules(typecheck(plan)) == {"executable_leaf"}


def test_reused_task_inherits_its_implementations():
    task_id = uuid4()
    plan = valid_plan()
    plan.nodes[1].implementations = []
    plan.nodes[1].existing_task_id = task_id

    context = TypecheckContext(
        implementation_counts={task_id: 2}, known_task_ids=frozenset({task_id})
    )
    assert typecheck(plan, context) == []


def test_reused_task_with_no_implementations_is_rejected():
    task_id = uuid4()
    plan = valid_plan()
    plan.nodes[1].implementations = []
    plan.nodes[1].existing_task_id = task_id

    context = TypecheckContext(
        implementation_counts={task_id: 0}, known_task_ids=frozenset({task_id})
    )
    problems = typecheck(plan, context)
    assert rules(problems) == {"executable_leaf"}
    assert "no enabled implementation" in problems[0].message


def test_reused_task_that_does_not_exist_is_rejected():
    plan = valid_plan()
    plan.nodes[1].implementations = []
    plan.nodes[1].existing_task_id = uuid4()
    problems = typecheck(plan, TypecheckContext.empty())
    assert rules(problems) == {"executable_leaf"}
    assert "not a live task node" in problems[0].message


# --- composite_interface ---------------------------------------------------


def test_composite_promising_an_unproduced_output_is_rejected():
    plan = valid_composite_plan()
    plan.nodes[0].output_schema = obj({"xlsx_path": "string"})
    problems = typecheck(plan)
    assert rules(problems) == {"composite_interface"}
    assert "nothing in its expansion produces" in problems[0].message


def test_composite_not_declaring_an_input_its_expansion_needs_is_rejected():
    """
    Owned by dataflow_closure, which reports it against the child that
    actually needs the input, and names the expansion it is in.
    """
    plan = valid_composite_plan()
    plan.nodes[0].expansion.nodes[0].input_schema = obj(
        {"pdf_path": "string", "password": "string"}
    )
    problems = typecheck(plan)
    assert rules(problems) == {"dataflow_closure"}
    assert "'password'" in problems[0].message
    assert "the expansion of 'c1'" in problems[0].message


def test_type_mismatch_inside_an_expansion_is_caught():
    """
    The regression that mattered: a seeded composite puts the whole workflow
    in an expansion, so an unchecked expansion means the reference workflow's
    entire chain of PRODUCES edges goes unvalidated while the plan reports
    clean.
    """
    plan = valid_composite_plan()
    # e1 now emits regions as a string; e2 still consumes an array.
    plan.nodes[0].expansion.nodes[0].output_schema = obj({"regions": "string"})
    problems = typecheck(plan)

    assert "type_compatibility" in rules(problems)
    assert any("the expansion of 'c1'" in p.message for p in problems)


def test_dangling_input_inside_an_expansion_is_caught():
    plan = valid_composite_plan()
    # e2 needs `regions`, but e1 no longer produces it and the composite
    # never declared it.
    plan.nodes[0].expansion.nodes[0].output_schema = obj({"something_else": "array"})
    problems = typecheck(plan)

    assert "dataflow_closure" in rules(problems)
    assert any("the expansion of 'c1'" in p.message for p in problems)


def test_dataflow_messages_read_as_english():
    """
    A rule is only as useful as the sentence it prints. This is the test that
    was missing when the message regressed to "...and external_inputs names
    not" -- every other assertion only checked that the property name
    appeared somewhere in the string.
    """
    top = valid_plan()
    top.external_inputs = []
    message = typecheck(top)[0].message
    assert message.endswith("external_inputs does not name it")

    nested = valid_composite_plan()
    nested.nodes[0].expansion.nodes[0].input_schema = obj(
        {"pdf_path": "string", "password": "string"}
    )
    message = typecheck(nested)[0].message
    assert message.endswith("the composite does not declare it")
    assert "the expansion of 'c1'" in message


def test_an_optional_composite_input_cannot_satisfy_a_required_child():
    """
    A caller may legally omit an input the composite marks optional, so it
    cannot be what satisfies a child that requires it -- otherwise the plan
    typechecks and the child dies mid-expansion at runtime instead.
    """
    plan = valid_composite_plan()
    plan.nodes[0].input_schema = {
        "type": "object",
        "properties": {"pdf_path": {"type": "string"}},
        "required": [],
    }
    assert "dataflow_closure" in rules(typecheck(plan))


def test_composite_without_an_expansion_is_rejected():
    plan = valid_composite_plan()
    plan.nodes[0].expansion = None
    assert rules(typecheck(plan)) == {"composite_interface"}


def test_leaf_carrying_an_expansion_is_rejected():
    plan = valid_composite_plan()
    plan.nodes[0].kind = "leaf"
    problems = typecheck(plan)
    assert "composite_interface" in rules(problems)


def test_composite_output_type_must_match():
    plan = valid_composite_plan()
    plan.nodes[0].output_schema = obj({"grid": "string"})  # expansion produces an array
    assert rules(typecheck(plan)) == {"composite_interface"}


# --- nesting_depth ---------------------------------------------------------


def test_two_levels_of_nesting_are_rejected():
    plan = valid_composite_plan()
    inner = PlanNode(
        ref="e3",
        name="inner_composite",
        kind="composite",
        input_schema=obj({"grid": "array"}),
        output_schema=obj({"rows": "array"}),
        expansion=Expansion(nodes=[node("e4", {"grid": "array"}, {"rows": "array"})], edges=[]),
    )
    plan.nodes[0].expansion.nodes.append(inner)
    plan.nodes[0].output_schema = obj({"grid": "array", "rows": "array"})

    problems = typecheck(plan)
    assert "nesting_depth" in rules(problems)
    assert any("one level of nesting" in p.message for p in problems)


# --- supporting machinery --------------------------------------------------


def test_topological_order_is_stable():
    plan = valid_plan()
    first = [n.ref for n in topological_order(plan.nodes, plan.edges)]
    second = [n.ref for n in topological_order(plan.nodes, plan.edges)]
    assert first == second == ["n1", "n2"]


def test_report_shape():
    report = typecheck_report(typecheck(valid_plan()))
    assert report == {"ok": True, "problems": [], "messages": []}

    plan = valid_plan()
    plan.nodes[1].implementations = []
    failing = typecheck_report(typecheck(plan))
    assert failing["ok"] is False
    assert failing["messages"][0].startswith("[executable_leaf]")
