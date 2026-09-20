"""
Pure-logic tests for app.stealth.pipe_format -- the exact pipe-delimited
`.stealth/` grammar (meta-harness directive Sec 23-31). No database, no
I/O. Proves both the exact grammar AND the directive's own §30 grep
claims (rg by stable id retrieves the complete record for that id).
"""
from __future__ import annotations

import re

from app.stealth.pipe_format import (
    ClaimLine,
    CollabLine,
    GoalLine,
    GoalRunLine,
    GroupLine,
    NodeLine,
    ProcedureLine,
    RunLine,
    RunStateLine,
    StepLine,
    VerifyLine,
    VerifyReqLine,
    render_claims_md,
    render_goal_run_md,
    render_goals_md,
    render_index_md,
    render_procedures_md,
    render_run_md,
    parse_goal_run_md,
)


# ===========================================================================
# claims.md
# ===========================================================================


def test_claim_line_exact_grammar():
    md = render_claims_md([
        ClaimLine("C-022", "ACTIVE", "generated-code", "repo", "schema/api.yaml is source-of-truth.", "CLAUDE.md", 2),
    ])
    lines = [ln for ln in md.splitlines() if ln.startswith("CLAIM|")]
    assert lines == [
        "CLAIM|C-022|ACTIVE|generated-code|repo|schema/api.yaml is source-of-truth.|source=CLAUDE.md|version=2",
    ]


def test_claims_md_empty_is_honest_not_fabricated():
    md = render_claims_md([])
    assert not any(ln.startswith("CLAIM|") for ln in md.splitlines())
    assert "(no claims)" in md


def test_claims_md_no_claim_leaks_a_pipe_or_newline_into_a_field():
    md = render_claims_md([
        ClaimLine("C-1", "ACTIVE", "t", "repo", "a | statement\nwith a newline and | pipes", "src|weird\nname", 1),
    ])
    body_lines = [ln for ln in md.splitlines() if ln.startswith("CLAIM|")]
    assert len(body_lines) == 1  # never forged into two rows
    assert len(body_lines[0].split("|")) == 8  # CLAIM + 7 fields, still exactly 8


def test_grep_claim_by_id_returns_exactly_one_complete_record():
    md = render_claims_md([
        ClaimLine("C-018", "ACTIVE", "auth", "repo", "auth statement", "AGENTS.md", 1),
        ClaimLine("C-022", "ACTIVE", "generated-code", "repo", "generated statement", "CLAUDE.md", 2),
    ])
    matches = [ln for ln in md.splitlines() if re.search(r"^CLAIM\|C-022\|", ln)]
    assert len(matches) == 1
    assert "generated statement" in matches[0]
    assert "C-018" not in matches[0]


# ===========================================================================
# procedures.md
# ===========================================================================


def test_procedure_step_verify_req_exact_grammar():
    md = render_procedures_md([
        ProcedureLine(
            "P-031", "ACTIVE", "generated-code", "global", "Modify generated API safely", 3,
            steps=[
                StepLine("S1", 1, "source_of_truth_resolution", "Identify source-of-truth", []),
                StepLine("S2", 2, "code_edit", "Modify source-of-truth", ["S1"]),
            ],
            verify_reqs=[VerifyReqLine("S2", "generated_consistency_verification", "generated-drift passes")],
        ),
    ])
    lines = md.splitlines()
    assert "PROCEDURE|P-031|ACTIVE|generated-code|global|Modify generated API safely|version=3" in lines
    assert "STEP|P-031|S1|1|source_of_truth_resolution|Identify source-of-truth|deps=-" in lines
    assert "STEP|P-031|S2|2|code_edit|Modify source-of-truth|deps=S1" in lines
    assert "VERIFY_REQ|P-031|S2|generated_consistency_verification|generated-drift passes" in lines


def test_procedures_md_empty_is_honest():
    md = render_procedures_md([])
    assert "(no procedures)" in md


def test_grep_steps_for_one_procedure_id():
    md = render_procedures_md([
        ProcedureLine("P-031", "ACTIVE", "t", "global", "n", 1, steps=[StepLine("S1", 1, "g", "d", [])]),
        ProcedureLine("P-044", "ACTIVE", "t", "global", "n2", 1, steps=[StepLine("S1", 1, "g2", "d2", [])]),
    ])
    matches = [ln for ln in md.splitlines() if re.search(r"^STEP\|P-031\|", ln)]
    assert len(matches) == 1
    assert "g2" not in matches[0]


# ===========================================================================
# run.md
# ===========================================================================


def _run_and_node(**node_overrides):
    node_kwargs = dict(
        node_id="N-003", status="RUNNING", name="Implement callback route",
        procedure_id="P-102", step_id="S3", binding="command", executor="frontier",
        deps=["N-002"], goal_id="G-014", grounded_goal_summary="implement OAuth callback route",
        inputs={"file": "src/auth/callback.py"},
        context_claims=["C-018@1"], access=[("filesystem", "write:src/auth/**")],
        expected_outcome="callback route handles OAuth redirect",
        verify=[VerifyLine("N-003", "V-001", "PASS", "deterministic_check", "code compiles", "E-91", "E-98")],
        owner="agent-a", lease_until="2026-09-15T13:00:00Z",
    )
    node_kwargs.update(node_overrides)
    run = RunLine("R-82", "RUNNING", "Add OAuth login", "P-102", 4)
    return run, NodeLine(**node_kwargs)


def test_run_header_exact_grammar():
    run, node = _run_and_node()
    md = render_run_md(run, [node])
    assert "RUN|R-82|RUNNING|Add OAuth login|procedure=P-102@4" in md.splitlines()


def test_node_line_exact_grammar():
    run, node = _run_and_node()
    md = render_run_md(run, [node])
    assert (
        "NODE|N-003|RUNNING|Implement callback route|goal=G-014|step=P-102:S3|binding=command|executor=frontier|deps=N-002"
        in md.splitlines()
    )


def test_goal_line_exact_grammar():
    run, node = _run_and_node()
    md = render_run_md(run, [node])
    assert "GOAL|N-003|G-014|implement OAuth callback route" in md.splitlines()


def test_no_goal_id_renders_dash_and_omits_goal_line():
    """Sec 25: goal_id is None when no real canonical Goal has been
    resolved for this node -- the NODE line's goal= field is the literal
    `-`, and no GOAL|... line is emitted at all (a summary with nothing
    real to anchor it is not rendered)."""
    run, node = _run_and_node(goal_id=None, grounded_goal_summary=None)
    md = render_run_md(run, [node])
    node_line = next(ln for ln in md.splitlines() if ln.startswith("NODE|"))
    assert "goal=-" in node_line
    assert not any(ln.startswith("GOAL|") for ln in md.splitlines())


def test_every_node_specific_line_repeats_the_node_id():
    """Directive Sec 27's own contract: `rg N-003 run.md` must retrieve
    all meaningful state for N-003 -- only possible if every line about
    that node literally contains its id."""
    run, node = _run_and_node()
    md = render_run_md(run, [node])
    node_section = "\n".join(ln for ln in md.splitlines() if ln and not ln.startswith("#") and not ln.startswith("RUN|"))
    for ln in node_section.splitlines():
        assert "N-003" in ln, f"line missing node id: {ln!r}"


def test_rg_node_id_retrieves_goal_input_context_access_outcome_verify_owner():
    run, node = _run_and_node()
    md = render_run_md(run, [node])
    matches = [ln for ln in md.splitlines() if re.search(r"N-003", ln)]
    kinds = {ln.split("|", 1)[0] for ln in matches}
    assert kinds == {"NODE", "GOAL", "INPUT", "CONTEXT", "ACCESS", "OUTCOME", "VERIFY", "OWNER"}


def test_rg_verify_node_prefix_retrieves_only_verification_lines():
    run, node = _run_and_node(verify=[
        VerifyLine("N-003", "V-001", "PASS", "deterministic_check", "code compiles", "E-91", "E-98"),
        VerifyLine("N-003", "V-002", "PENDING", "test", "integration test passes"),
    ])
    md = render_run_md(run, [node])
    matches = [ln for ln in md.splitlines() if re.match(r"^VERIFY\|N-003\|", ln)]
    assert len(matches) == 2
    assert any("V-001" in m and "PASS" in m for m in matches)
    assert any("V-002" in m and "PENDING" in m for m in matches)
    # pending verification with no evidence renders the literal "none", never fabricated
    pending = next(m for m in matches if "V-002" in m)
    assert "evidence=none" in pending


def test_two_nodes_never_cross_contaminate_lines():
    run = RunLine("R-1", "RUNNING", "obj", "P-1", 1)
    n1 = NodeLine("N-001", "DONE", "first", "P-1", "S1", None, "deterministic", verify=[
        VerifyLine("N-001", "V-001", "PASS", "deterministic_check", "first check"),
    ])
    n2 = NodeLine("N-002", "RUNNING", "second", "P-1", "S2", None, "frontier", deps=["N-001"], verify=[
        VerifyLine("N-002", "V-001", "PENDING", "test", "second check"),
    ])
    md = render_run_md(run, [n1, n2])
    n1_lines = [ln for ln in md.splitlines() if re.match(r"^(NODE|VERIFY)\|N-001\|", ln)]
    assert all("second" not in ln and "N-002" not in ln for ln in n1_lines)


def test_run_md_empty_nodes_is_honest():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    md = render_run_md(run, [])
    assert "(no nodes)" in md


def test_unbound_step_renders_literal_dash_not_fabricated():
    run, node = _run_and_node(binding=None)
    md = render_run_md(run, [node])
    assert "binding=-" in [ln for ln in md.splitlines() if ln.startswith("NODE|")][0]


# ===========================================================================
# run.md -- COLLAB / COLLAB_SUMMARY (migration 90/91)
# ===========================================================================


def test_collab_note_and_question_render_run_level_and_node_level():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    n0 = NodeLine("N0", "pending", "step", "P-1", "S0", None, "frontier")
    note = CollabLine("c-note", "NOTE", "agent-a", "run-level note", "2026-01-01T00:00:00+00:00")
    question = CollabLine("c-q", "QUESTION", "agent-a", "is this safe?", "2026-01-01T00:00:01+00:00", node_id="N0")
    md = render_run_md(run, [n0], [note, question])

    lines = md.splitlines()
    node_start = next(i for i, ln in enumerate(lines) if ln.startswith("NODE|N0|"))
    # the run-level NOTE renders before the first NODE block ...
    assert any(ln.startswith("COLLAB|c-note|NOTE|-|") for ln in lines[:node_start])
    # ... and the node-scoped QUESTION renders inline under N0, not up top.
    assert not any("c-q" in ln for ln in lines[:node_start])
    assert any(ln.startswith("COLLAB|c-q|QUESTION|N0|") for ln in lines[node_start:])


def test_collab_summary_counts_unanswered_question_and_open_records():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    question = CollabLine("c-q", "QUESTION", "a", "q?", "t")
    blocker = CollabLine("c-b", "BLOCKER", "a", "blocked", "t")
    handoff = CollabLine("c-h", "HANDOFF", "a", "handing off", "t")
    md = render_run_md(run, [], [question, blocker, handoff])
    assert "COLLAB_SUMMARY|open_blockers=1|pending_handoffs=1|unanswered_questions=1" in md


def test_collab_summary_decrements_once_a_question_is_answered():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    question = CollabLine("c-q", "QUESTION", "a", "q?", "t0")
    answer = CollabLine("c-a", "ANSWER", "b", "yes", "t1", answers_id="c-q")
    md = render_run_md(run, [], [question, answer])
    assert "unanswered_questions=0" in md
    assert any(ln.startswith("COLLAB|c-a|ANSWER|") and "answers=c-q" in ln for ln in md.splitlines())


def test_collab_summary_decrements_once_a_blocker_is_resolved():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    blocker = CollabLine("c-b", "BLOCKER", "a", "blocked on creds", "t0")
    resolved = CollabLine("c-r", "BLOCKER_RESOLVED", "a", "creds arrived", "t1", answers_id="c-b")
    md = render_run_md(run, [], [blocker, resolved])
    assert "open_blockers=0" in md
    assert any(ln.startswith("COLLAB|c-r|BLOCKER_RESOLVED|") and "answers=c-b" in ln for ln in md.splitlines())


def test_collab_summary_decrements_once_a_handoff_is_accepted():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    handoff = CollabLine("c-h", "HANDOFF", "a", "please take node 1", "t0", target_agent_id="agent-b")
    accepted = CollabLine("c-acc", "HANDOFF_ACCEPTED", "agent-b", "picked it up", "t1", answers_id="c-h")
    md = render_run_md(run, [], [handoff, accepted])
    assert "pending_handoffs=0" in md
    assert any(ln.startswith("COLLAB|c-acc|HANDOFF_ACCEPTED|") and "answers=c-h" in ln for ln in md.splitlines())


def test_collab_summary_a_resolved_blocker_does_not_affect_a_separate_open_one():
    run = RunLine("R-1", "PENDING", "obj", "P-1", 1)
    resolved_blocker = CollabLine("c-b1", "BLOCKER", "a", "first blocker", "t0")
    resolution = CollabLine("c-r", "BLOCKER_RESOLVED", "a", "fixed", "t1", answers_id="c-b1")
    other_blocker = CollabLine("c-b2", "BLOCKER", "a", "second, still open", "t2")
    md = render_run_md(run, [], [resolved_blocker, resolution, other_blocker])
    assert "open_blockers=1" in md


# ===========================================================================
# goal_run.md (Prompt 2 Sec 12/13)
# ===========================================================================


def test_goal_run_md_exact_grammar():
    nodes = [
        GoalRunLine(
            goal_id="G-1", kind="step", status="success",
            binding="command", verification_state="checked",
        ),
        GoalRunLine(
            goal_id="G-parent", kind="procedure", status="failure",
            procedure_id=None, human_intervention_needed=True,
        ),
    ]
    md = render_goal_run_md("E-1", "failure", nodes)
    lines = md.splitlines()
    assert "GOAL_RUN|E-1|failure" in lines
    assert "GOAL_NODE|G-1|step|success|binding=command|proc=-|verify=checked|human_intervention=False|resumed=False" in lines
    assert "GOAL_NODE|G-parent|procedure|failure|binding=-|proc=-|verify=-|human_intervention=True|resumed=False" in lines


def test_goal_run_md_empty_nodes_is_honest():
    md = render_goal_run_md("E-1", "success", [])
    assert "(no nodes)" in md


def test_goal_run_md_never_fabricates_a_missing_binding_or_procedure():
    nodes = [GoalRunLine(goal_id="G-1", kind="step", status="failure")]
    md = render_goal_run_md("E-1", "failure", nodes)
    node_line = [ln for ln in md.splitlines() if ln.startswith("GOAL_NODE|")][0]
    assert "binding=-" in node_line
    assert "proc=-" in node_line
    assert "verify=-" in node_line


def test_goal_run_md_marks_resumed_nodes_honestly():
    nodes = [GoalRunLine(goal_id="G-1", kind="step", status="success", resumed_from_journal=True)]
    md = render_goal_run_md("E-1", "success", nodes)
    assert "resumed=True" in md


def test_goal_run_md_renders_real_artifact_lines():
    nodes = [GoalRunLine(
        goal_id="G-1", kind="step", status="success",
        artifacts=[{"filename": "out.txt", "sha256": "abc123", "size_bytes": 11}],
    )]
    md = render_goal_run_md("E-1", "success", nodes)
    assert "ARTIFACT|G-1|out.txt|sha256=abc123|size=11" in md.splitlines()


def test_goal_run_md_no_artifacts_emits_no_artifact_lines():
    nodes = [GoalRunLine(goal_id="G-1", kind="step", status="success")]
    md = render_goal_run_md("E-1", "success", nodes)
    assert not any(ln.startswith("ARTIFACT|") for ln in md.splitlines())


# ---------------------------------------------------------------------
# parse_goal_run_md -- the real inverse of render_goal_run_md
# ---------------------------------------------------------------------


def test_parse_goal_run_md_round_trips_every_field():
    nodes = [
        GoalRunLine(
            goal_id="G-1", kind="step", status="success",
            binding="command", procedure_id="P-1", verification_state="checked",
            human_intervention_needed=True, resumed_from_journal=True,
        ),
    ]
    md = render_goal_run_md("E-1", "success", nodes)
    parsed = parse_goal_run_md(md)
    assert parsed["execution_id"] == "E-1"
    assert parsed["outcome"] == "success"
    assert len(parsed["nodes"]) == 1
    n = parsed["nodes"][0]
    assert n["goal_id"] == "G-1"
    assert n["kind"] == "step"
    assert n["status"] == "success"
    assert n["binding"] == "command"
    assert n["procedure_id"] == "P-1"
    assert n["verification_state"] == "checked"
    assert n["human_intervention_needed"] is True
    assert n["resumed_from_journal"] is True


def test_parse_goal_run_md_never_fabricates_missing_fields():
    nodes = [GoalRunLine(goal_id="G-1", kind="step", status="failure")]
    md = render_goal_run_md("E-1", "failure", nodes)
    n = parse_goal_run_md(md)["nodes"][0]
    assert n["binding"] is None
    assert n["procedure_id"] is None
    assert n["verification_state"] is None
    assert n["human_intervention_needed"] is False
    assert n["resumed_from_journal"] is False


def test_parse_goal_run_md_recovers_real_artifacts_per_node():
    nodes = [GoalRunLine(
        goal_id="G-1", kind="step", status="success",
        artifacts=[{"filename": "out.txt", "sha256": "abc123", "size_bytes": 11}],
    )]
    md = render_goal_run_md("E-1", "success", nodes)
    n = parse_goal_run_md(md)["nodes"][0]
    assert n["artifacts"] == [{"filename": "out.txt", "sha256": "abc123", "size_bytes": "11"}]


def test_parse_goal_run_md_handles_multiple_nodes_in_order():
    nodes = [
        GoalRunLine(goal_id="G-1", kind="step", status="success", binding="command"),
        GoalRunLine(goal_id="G-2", kind="human", status="needs_input"),
    ]
    md = render_goal_run_md("E-1", "success", nodes)
    parsed = parse_goal_run_md(md)
    assert [n["goal_id"] for n in parsed["nodes"]] == ["G-1", "G-2"]


def test_parse_goal_run_md_on_the_no_nodes_case_returns_empty_list():
    md = render_goal_run_md("E-1", "success", [])
    parsed = parse_goal_run_md(md)
    assert parsed["execution_id"] == "E-1"
    assert parsed["nodes"] == []


# ===========================================================================
# index.md
# ===========================================================================


def test_index_md_exact_grammar():
    md = render_index_md(
        repo="StealthLab", revision=184, active_run="R-82",
        claim_groups=[GroupLine("generated-code", ["C-022", "C-037"])],
        procedure_groups=[GroupLine("generated-code", ["P-031"])],
                run_states=[RunStateLine("RUNNING", ["N-003"]), RunStateLine("BLOCKED", [])],
    )
    lines = md.splitlines()
    assert "REPO|StealthLab" in lines
    assert "REVISION|184" in lines
    assert "ACTIVE_RUN|R-82" in lines
    assert "CLAIM_GROUP|generated-code|C-022 C-037" in lines
    assert "PROCEDURE_GROUP|generated-code|P-031" in lines
    assert "RUN_STATE|RUNNING|N-003" in lines
    assert "RUN_STATE|BLOCKED|-" in lines


def test_index_md_no_active_run_renders_literal_none_not_fabricated():
    md = render_index_md(
        repo="R", revision=1, active_run=None,
        claim_groups=[], procedure_groups=[], run_states=[],
    )
    assert "ACTIVE_RUN|none" in md.splitlines()


def test_grep_claim_group_by_topic():
    md = render_index_md(
        repo="R", revision=1, active_run=None,
        claim_groups=[GroupLine("auth", ["C-018"]), GroupLine("generated-code", ["C-022", "C-037"])],
        procedure_groups=[], run_states=[],
    )
    matches = [ln for ln in md.splitlines() if re.match(r"^CLAIM_GROUP\|generated-code\|", ln)]
    assert matches == ["CLAIM_GROUP|generated-code|C-022 C-037"]


def test_index_md_goal_group_uses_space_separator_not_the_directives_own_comma_example():
    """execu.md's own worked example shows `GOAL_GROUP|reference-search|
    G-014,G-231` (comma-separated) -- deliberately NOT followed here, per
    render_index_md's own docstring: one separator convention across
    every *_GROUP line in this file beats matching one inconsistent
    example verbatim."""
    md = render_index_md(
        repo="R", revision=1, active_run=None,
        claim_groups=[], procedure_groups=[], run_states=[],
        goal_groups=[GroupLine("reference-search", ["G-014", "G-231"])],
    )
    assert "GOAL_GROUP|reference-search|G-014 G-231" in md.splitlines()
    assert not any("G-014,G-231" in ln for ln in md.splitlines())


# ===========================================================================
# goals.md
# ===========================================================================


def test_goal_line_and_detail_and_aliases_exact_grammar():
    md = render_goals_md([
        GoalLine(
            goal_id="G-014", status="active", scope="global", name="Find references", version=2,
            expected_outcome="a complete list of callers", verification_summary="manual review",
            aliases=["find callers", "find usages"],
        ),
    ])
    lines = md.splitlines()
    assert "GOAL|G-014|active|global|Find references|version=2" in lines
    assert "GOAL_DETAIL|G-014|outcome=a complete list of callers|verification=manual review" in lines
    assert "ALIASES|G-014|find callers,find usages" in lines


def test_goals_md_empty_is_honest_not_fabricated():
    md = render_goals_md([])
    assert not any(ln.startswith("GOAL|") for ln in md.splitlines())
    assert "(no goals)" in md


def test_goal_with_no_detail_or_aliases_omits_those_lines():
    md = render_goals_md([GoalLine(goal_id="G-1", status="candidate", scope="repo", name="x", version=1)])
    lines = md.splitlines()
    assert any(ln.startswith("GOAL|G-1|") for ln in lines)
    assert not any(ln.startswith("GOAL_DETAIL|") for ln in lines)
    assert not any(ln.startswith("ALIASES|") for ln in lines)


def test_grep_goal_by_id_returns_exactly_one_complete_record():
    md = render_goals_md([
        GoalLine(goal_id="G-1", status="active", scope="global", name="first", version=1),
        GoalLine(goal_id="G-2", status="active", scope="global", name="second", version=1),
    ])
    matches = [ln for ln in md.splitlines() if re.match(r"^GOAL\|G-2\|", ln)]
    assert len(matches) == 1
    assert "second" in matches[0]
