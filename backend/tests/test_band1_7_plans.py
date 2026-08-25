"""Band 1.7 proving tests -- plan persistence contracts, offline (no DB).

Covers:
  Appendix C #1   every execution references an exact ExecutionPlan:
                  DDL leaves execution_plan_id NOT NULL (no nullable
                  path), validate_execution_binding refuses planless
                  payloads, and a replay REBINDS the identical plan
                  (same inputs -> identical content_hash -> existing row).
  Appendix C #2   every ExecutionPlan references an exact Procedure
                  version: plan carries {procedure_id, version}; the
                  validator and the migration's composite FK both reject
                  versionless references.
  Appendix C #17  instantiation never silently modifies the source
                  Procedure: compile fingerprints the procedure payload
                  without mutating it; drift is detected and refused;
                  changed inputs mean a NEW plan identity, never an edit.

Migration assertions are static text checks against db/23_plan_persistence.sql
(the DB application itself is integration work -- board queue item 2);
the contract logic itself is proven by running it, per Band 1's preamble:
"tests assert the new mechanism directly."
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.execution.plans import (
    PlanViolation,
    assert_procedure_unmodified,
    canonical_json,
    compile_plan,
    find_rebindable_plan,
    hash_procedure_version,
    sha256_hex,
    validate_execution_binding,
    validate_graph,
    validate_node_scope,
)
from app.models.plan import (
    ExecutionPlan,
    PlanNode,
    ProcedureRef,
    TaskGraph,
)

PROC_ID = uuid4()
PROC_ROW_ID = uuid4()
CLAIM_ID = uuid4()

PROCEDURE_PAYLOAD = {
    "id": PROC_ID,
    "version": 3,
    "name": "deploy-api",
    "goal": "ship the API to staging",
    "steps": [
        {"order": 1, "goal": "run migrations"},
        {"order": 2, "goal": "restart workers"},
    ],
}

TWO_NODE_DAG = [
    {"order": 0, "goal": "run migrations", "deps": []},
    {"order": 1, "goal": "restart workers", "deps": [0]},
]


def _compile(**overrides):
    kwargs = dict(
        procedure_id=PROC_ID,
        procedure_version=3,
        procedure_row_id=PROC_ROW_ID,
        procedure_payload=PROCEDURE_PAYLOAD,
        task_description="deploy commit abc123",
        scope_type="repository",
        scope_entity_id="repo_42",
        parameters={"env": "staging"},
        resolved_claims=[{"claim_id": CLAIM_ID, "version": 2}],
        implementations={"0": "impl_terraform"},
        safety_check="passed",
        verification_plan={"post": ["healthcheck"]},
        nodes=TWO_NODE_DAG,
        extractor_version="plan_compiler@1",
    )
    kwargs.update(overrides)
    return compile_plan(**kwargs)


# --------------------------------------------- invariant #2: exact versions


def test_versionless_reference_rejected():
    with pytest.raises(PlanViolation, match="version"):
        compile_plan(
            procedure_id=PROC_ID,
            procedure_version=None,
            procedure_row_id=PROC_ROW_ID,
            procedure_payload=PROCEDURE_PAYLOAD,
            task_description="t",
            extractor_version="plan_compiler@1",
        )


def test_anonymous_procedure_reference_rejected():
    with pytest.raises(PlanViolation, match="procedure_id is required"):
        compile_plan(
            procedure_id=None,
            procedure_version=3,
            procedure_row_id=PROC_ROW_ID,
            procedure_payload=PROCEDURE_PAYLOAD,
            task_description="t",
            extractor_version="plan_compiler@1",
        )


def test_nonpositive_version_unrepresentable():
    for bad in (0, -1):
        with pytest.raises(PydanticValidationError):
            ProcedureRef(procedure_id=PROC_ID, version=bad)


# ---------------------------- invariant #17: source never silently edited


def test_compile_never_mutates_the_source_payload():
    snapshot = copy.deepcopy(PROCEDURE_PAYLOAD)
    compiled = _compile()
    assert PROCEDURE_PAYLOAD == snapshot, "instantiation modified its source"
    assert (
        compiled.plan.procedure_content_hash
        == hash_procedure_version(PROCEDURE_PAYLOAD)
    )


def test_drifted_source_refuses_to_rebind():
    current = hash_procedure_version({**PROCEDURE_PAYLOAD, "steps": []})
    with pytest.raises(PlanViolation, match="new procedure version"):
        assert_procedure_unmodified(
            hash_procedure_version(PROCEDURE_PAYLOAD), current
        )


def test_unchanged_source_rebinds_cleanly():
    assert_procedure_unmodified(
        hash_procedure_version(PROCEDURE_PAYLOAD),
        hash_procedure_version(PROCEDURE_PAYLOAD),
    )

# ------------------------------------------ invariant #1: bindings + rebind


def test_planless_execution_payload_refused():
    with pytest.raises(PlanViolation, match="execution_plan_id is required"):
        validate_execution_binding(
            execution_plan_id=None,
            task_graph_id=uuid4(),
            procedure_id=PROC_ID,
            procedure_version=3,
        )


def test_graphless_execution_payload_refused():
    with pytest.raises(PlanViolation, match="task_graph_id is required"):
        validate_execution_binding(
            execution_plan_id=uuid4(),
            task_graph_id=None,
            procedure_id=PROC_ID,
            procedure_version=3,
        )


def test_binding_snapshot_must_agree_with_its_plan():
    compiled = _compile()
    ok = validate_execution_binding(
        execution_plan_id=compiled.plan.id,
        task_graph_id=compiled.graph.id,
        procedure_id=PROC_ID,
        procedure_version=3,
        plan=compiled.plan,
    )
    assert ok["procedure_version"] == 3
    with pytest.raises(PlanViolation, match="disagrees with its plan"):
        validate_execution_binding(
            execution_plan_id=compiled.plan.id,
            task_graph_id=compiled.graph.id,
            procedure_id=PROC_ID,
            procedure_version=4,  # drifted snapshot of the SAME plan run
            plan=compiled.plan,
        )


def test_binding_foreign_plan_or_graph_refused():
    compiled = _compile()
    with pytest.raises(PlanViolation, match="different plan"):
        validate_execution_binding(
            execution_plan_id=uuid4(),
            task_graph_id=compiled.graph.id,
            procedure_id=PROC_ID,
            procedure_version=3,
            plan=compiled.plan,
        )
    with pytest.raises(PlanViolation, match="does not own"):
        validate_execution_binding(
            execution_plan_id=compiled.plan.id,
            task_graph_id=uuid4(),
            procedure_id=PROC_ID,
            procedure_version=3,
            plan=compiled.plan,
        )


def test_replay_rebinds_the_identical_plan():
    """Appendix C #1's proving clause: same inputs -> identical hash ->
    the EXISTING frozen row is rebound (not a forked twin)."""
    first = _compile()
    second = _compile()  # fresh process-fresh ids, identical semantics

    assert second.plan.content_hash == first.plan.content_hash
    assert second.graph.graph_hash == first.graph.graph_hash
    assert second.plan.id != first.plan.id  # identity lives in content, not uuids

    stored_rows = [first.plan.to_row()]
    rebound = find_rebindable_plan(stored_rows, second)
    assert rebound is not None and rebound["id"] == first.plan.id

    # ...and a DIFFERENT compile must NOT silently rebind it.
    drifted = _compile(parameters={"env": "production"})
    assert find_rebindable_plan(stored_rows, drifted) is None


def test_changed_inputs_mean_a_new_plan_identity():
    base = _compile()
    variants = [
        ("task_description", "deploy commit def456"),
        ("parameters", {"env": "production"}),
        ("resolved_claims", [{"claim_id": CLAIM_ID, "version": 3}]),
        ("implementations", {"0": "impl_bash"}),
        ("safety_check", "requires_review"),
        ("nodes", [n | {"goal": n["goal"] + "!"} for n in TWO_NODE_DAG]),
        ("scope_entity_id", "repo_43"),
        ("extractor_version", "plan_compiler@2"),
    ]
    for field, value in variants:
        assert _compile(**{field: value}).plan.content_hash != base.plan.content_hash, (
            f"changing {field} did not change the plan's identity"
        )


def test_key_order_and_whitespace_cannot_change_identity():
    assert _compile(parameters={"a": 1, "b": 2}).plan.content_hash == _compile(
        parameters={"b": 2, "a": 1}
    ).plan.content_hash
    assert sha256_hex(canonical_json({"x": [1, 2]})) == sha256_hex(canonical_json({"x": [1, 2]}))

# ------------------------------------------------- graph shape validation


def test_empty_graph_refused():
    with pytest.raises(PlanViolation, match="at least one task node"):
        _compile(nodes=[])


def test_duplicate_orders_refused():
    dup = [
        {"order": 0, "goal": "a", "deps": []},
        {"order": 0, "goal": "b", "deps": []},
    ]
    with pytest.raises(PlanViolation, match="duplicate node orders"):
        validate_graph(dup)


def test_self_dependency_refused():
    with pytest.raises(PlanViolation, match="depends on itself"):
        validate_graph([{"order": 0, "goal": "a", "deps": [0]}])


def test_unknown_dependency_refused():
    with pytest.raises(PlanViolation, match="not in its graph"):
        validate_graph([{"order": 0, "goal": "a", "deps": [7]}])


def test_dependency_cycle_refused():
    cycle = [
        {"order": 0, "goal": "a", "deps": [1]},
        {"order": 1, "goal": "b", "deps": [0]},
    ]
    with pytest.raises(PlanViolation, match="cycle"):
        validate_graph(cycle)


def test_unknown_node_class_refused():
    with pytest.raises(PlanViolation, match="node_class"):
        validate_graph([{"order": 0, "goal": "a", "node_class": "vibes"}])


def test_diamond_dag_accepted():
    diamond = [
        {"order": 0, "goal": "a", "deps": []},
        {"order": 1, "goal": "b", "deps": [0]},
        {"order": 2, "goal": "c", "deps": [0]},
        {"order": 3, "goal": "d", "deps": [1, 2]},
    ]
    assert len(validate_graph(diamond)) == 4


# --------------------------------------- scope narrowing (spec §24: never widen)


def test_narrower_node_scope_accepted():
    # spec's own worked example: repository plan, user-gated high-risk node
    validate_node_scope("repository", "repo_42", "user", "alice", where="n0")


def test_wider_node_scope_rejected():
    with pytest.raises(PlanViolation, match="never widen"):
        validate_node_scope("user", "alice", "repository", "repo_42", where="n0")


def test_sideways_entity_switch_rejected():
    with pytest.raises(PlanViolation, match="not narrowing"):
        validate_node_scope("repository", "repo_42", "repository", "repo_43", where="n0")


def test_explicit_scope_against_unrecorded_plan_scope_rejected():
    with pytest.raises(PlanViolation, match="record the plan scope first"):
        validate_node_scope(None, None, "project", "p1", where="n0")


def test_compile_rejects_widening_node():
    widening = TWO_NODE_DAG + [
        {"order": 2, "goal": "org-wide sweep", "deps": [],
         "scope_type": "organization", "scope_entity_id": "acme"},
    ]
    with pytest.raises(PlanViolation, match="never widen"):
        _compile(nodes=widening)


# --------------------------------------------------- V0 / derived discipline


@pytest.mark.parametrize("missing", [{"extractor_version": ""}, {"extractor_version": None}])
def test_unstamped_compiler_refused(missing):
    with pytest.raises(PlanViolation, match="extractor_version"):
        _compile(**missing)


def test_blank_task_description_refused():
    with pytest.raises(PlanViolation, match="task_description"):
        _compile(task_description="   ")


def test_unknown_safety_check_refused():
    with pytest.raises(PlanViolation, match="safety_check"):
        _compile(safety_check="yolo")


# ------------------------------------------------------------ model round trips


def test_plan_and_graph_rows_round_trip():
    compiled = _compile()
    plan_rt = ExecutionPlan.from_row(compiled.plan.to_row())
    assert plan_rt == compiled.plan
    graph_rt = TaskGraph.from_row(compiled.graph.to_row())
    assert graph_rt == compiled.graph


def test_nodes_round_trip_through_json_payloads():
    node = PlanNode(order=5, goal="g", deps=[4], scope_type="user", scope_entity_id="u")
    rt = PlanNode.from_row(node.model_dump(mode="json"))
    assert rt == node


# ------------------------------------- migration static checks (db/23 text)


def _ddl() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "23_plan_persistence.sql"
    return p.read_text(encoding="utf-8")


def test_ddl_executions_have_no_nullable_plan_path():
    ddl = _ddl()
    section = ddl.split("CREATE TABLE IF NOT EXISTS executions")[1].split(";")[0]
    assert re.search(r"execution_plan_id\s+UUID\s+NOT NULL", section), (
        "invariant #1: executions must have no nullable execution_plan_id"
    )
    assert re.search(r"task_graph_id\s+UUID\s+NOT NULL", section)
    assert re.search(r"procedure_version\s+INTEGER\s+NOT NULL", section)


def test_ddl_freeze_triggers_cover_all_three_tables():
    ddl = _ddl()
    for t in ("execution_plans", "task_graphs", "executions"):
        assert f"tg_{t}_frozen" in ddl, t
    assert "BEFORE UPDATE OR DELETE ON" in ddl
    assert "sl_raise_frozen" in ddl


def test_ddl_scope_constraints_match_scope_design():
    ddl = _ddl()
    # execution_plans and executions carry their own scope columns + CHECKs;
    # task_graphs deliberately has NONE — nodes inherit plan scope and may
    # narrow, never widen (spec §24). Regression pin for the engine-caught bug
    # where a boilerplate constraint referenced columns task_graphs omits:
    for t in ("execution_plans", "executions"):
        assert f"scope_type_chk_{t}" in ddl, t
    assert "scope_type_chk_task_graphs" not in ddl, (
        "task_graphs inherits plan scope; a scope CHECK here cannot compile"
    )


def test_ddl_has_no_backfill_statements():
    ddl = _ddl()
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", ddl, re.IGNORECASE), (
        "fresh-start ruling: migration must not contain data backfills "
        "(trigger DDL mentioning UPDATE is fine; SET-assignments are not)"
    )


def test_ddl_records_the_one_graph_per_plan_rule():
    assert re.search(r"execution_plan_id\s+UUID\s+NOT NULL UNIQUE", _ddl())


