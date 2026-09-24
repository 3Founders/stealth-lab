"""
goal_tree_to_knowledge (final_architecture.md): the resolved Goal tree becomes
the knowledge find_ways returns -- every step in full, alternatives kept, no
order or node ids invented. Also the binding-needs -> judge condition rule.
Pure, no DB.
"""
from __future__ import annotations

from app.execution.goal_knowledge import goal_tree_to_knowledge, step_check, step_needs
from app.execution.goal_resolution import ResolvedGoalNode
from app.execution.repo_facts import binding_conditions

PY_STEP = {
    "order": 0, "goal": "Validate office documents.", "description": "Run validate.py",
    "binding": {"kind": "source_artifact", "runtime": "python", "entrypoint": "scripts/validate.py",
                "sandbox_policy": "isolated", "source_artifact": "sa-1"},
    "source_locator": {"uri": "https://example.test/validate.py", "path": "scripts/validate.py"},
    "expected_outcome": None,
}


def _tree():
    sub_step = {"order": 0, "goal": "Install docx", "binding": {"kind": "command", "command": "npm i docx"},
                "expected_outcome": "docx in package.json"}
    sub = ResolvedGoalNode(
        goal_id="G-sub", goal_name="Install the docx package", depth=1, chosen="procedure",
        procedure={"id": "V-2", "procedure_id": "P-2", "name": "npm install", "version": 1, "steps": [sub_step]},
        children=[ResolvedGoalNode(goal_id="G-sub#s0", goal_name="Install docx", depth=2, chosen="step",
                                   step=sub_step)],
    )
    steps = [
        {"order": 0, "goal": "Install the docx package"},                     # -> subgoal G-sub
        {"order": 1, "goal": "Write build.js that creates a Document", "description": "use new Document()"},
        PY_STEP | {"order": 2},
    ]
    return ResolvedGoalNode(
        goal_id="G-root", goal_name="Create a DOCX document", depth=0, chosen="procedure",
        procedure={"id": "V-1", "procedure_id": "P-1", "name": "docx-js", "version": 3, "steps": steps,
                   "description": "Make .docx files with docx-js",
                   "repo_fit": {"verdict": "APPLICABLE", "supporting_fact_ids": ["R-002"]}},
        verification_requirement={"check": "out.docx opens"},
        procedure_alternates=[{"id": "V-9", "procedure_id": "P-9", "name": "python-docx", "version": 1}],
        children=[
            sub,
            ResolvedGoalNode(goal_id="-", goal_name="Write build.js that creates a Document", depth=1,
                             chosen="unresolved", unresolved_reason="step's goal text does not match any canonical Goal"),
            ResolvedGoalNode(goal_id="G-root#s2", goal_name="Validate", depth=1, chosen="step", step=PY_STEP),
        ],
        rationale="selected procedure 'docx-js'",
    )


def test_every_step_comes_back_in_full_with_its_kind():
    k = goal_tree_to_knowledge(_tree())
    root, sub = k["procedures"]
    assert [s["kind"] for s in root["steps"]] == ["subgoal", "instruction", "action"]
    assert root["steps"][0]["subgoal_id"] == "G-sub" and sub["parent"] == {"goal_id": "G-root", "step_order": 0}
    # an unmatched, unbound step is an instruction the agent can do -- not a failure
    assert root["steps"][1]["do"] == "Write build.js that creates a Document"
    assert "canonical Goal" in root["steps"][1]["note"]
    act = root["steps"][2]
    assert act["source_locator"]["uri"] == "https://example.test/validate.py"
    assert act["needs"] == {"runtime": "python", "entrypoint": "scripts/validate.py", "sandbox_policy": "isolated"}
    assert act["check"] is None  # the Procedure gives none: the planner writes one, nothing invented


def test_choice_carries_why_alternatives_and_repo_fit():
    k = goal_tree_to_knowledge(_tree())
    root = k["procedures"][0]
    assert k["goal"] == {"goal_id": "G-root", "name": "Create a DOCX document", "check": {"check": "out.docx opens"}}
    assert root["what_it_does"] == "Make .docx files with docx-js"
    assert root["alternatives"] == [{"procedure_id": "P-9", "version_id": "V-9", "name": "python-docx", "version": 1}]
    assert root["repo_fit"]["supporting_fact_ids"] == ["R-002"]
    assert root["why_chosen"] == "selected procedure 'docx-js'"


def test_no_node_ids_or_order_are_invented():
    k = goal_tree_to_knowledge(_tree())
    text = repr(k)
    assert "N-1" not in text and "node_id" not in text and "deps" not in text


def test_unresolved_root_is_reported_not_papered_over():
    k = goal_tree_to_knowledge(ResolvedGoalNode(goal_id="G", goal_name="x", depth=0, chosen="unresolved",
                                                unresolved_reason="no procedure linked to this goal"))
    assert k["procedures"] == []
    assert k["unresolved"] == [{"goal_id": "G", "goal_name": "x", "parent": None,
                                "reason": "no procedure linked to this goal"}]


def test_step_check_prefers_expected_outcome_then_verifier():
    assert step_check({"expected_outcome": "a", "verifier": "b"}) == "a"
    assert step_check({"binding": {"verifier": {"cmd": "t"}}}) == {"cmd": "t"}
    assert step_check({}) is None
    assert step_needs({"goal": "no binding"}) == {}


def test_binding_needs_become_implementation_binding_conditions():
    conds = binding_conditions({"steps": [PY_STEP, PY_STEP | {"order": 3},
                                          {"binding": {"kind": "adapter", "resources": {"network": True}}}]})
    assert [(c.text, c.kind) for c in conds] == [
        ("The repository can run python code", "IMPLEMENTATION_BINDING"),
        ("A step needs network access", "IMPLEMENTATION_BINDING"),
    ]
    assert binding_conditions({"steps": [{"goal": "plain"}]}) == []


def test_selector_sends_binding_needs_and_only_demotes_on_them():
    import asyncio

    from app.execution.repo_facts import RepoFactsProcedureSelector
    from app.services.applicability_judge import ApplicabilityJudgment

    seen = {}

    class Judge:
        async def judge_batch(self, goal, inputs):
            seen.update({i.candidate_id: [(c.text, c.kind) for c in i.conditions] for i in inputs})
            return [
                ApplicabilityJudgment(candidate_id="py", goal_or_query=goal, applicability_probability=0.1,
                                      contradiction_probability=0.95, preconditions_met_probability=0.1,
                                      verdict="INAPPLICABLE", blocking_claim_ids=["R-009"]),
                ApplicabilityJudgment(candidate_id="js", goal_or_query=goal, applicability_probability=0.9,
                                      contradiction_probability=0.0, preconditions_met_probability=0.9,
                                      verdict="APPLICABLE", supporting_claim_ids=["R-001"]),
            ]

    facts = [{"claim_id": "R-001", "statement": "Node 20.11"},
             {"claim_id": "R-009", "statement": "No Python toolchain"}]
    py = {"id": "py", "name": "python validator", "steps": [PY_STEP]}
    js = {"id": "js", "name": "docx-js", "steps": [{"goal": "write build.js"}]}
    sel = RepoFactsProcedureSelector(claims=facts, judge=Judge())
    kept, rejected = asyncio.run(sel(goal_name="validate", feasible=[py, js]))
    assert seen["py"] == [("The repository can run python code", "IMPLEMENTATION_BINDING")]
    # a contradicted runtime need moves it to the back; it is not disqualified
    assert [p["id"] for p in kept] == ["js", "py"] and rejected == []
