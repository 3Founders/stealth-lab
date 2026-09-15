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
    GroupLine,
    NodeLine,
    ProcedureLine,
    RunLine,
    RunStateLine,
    StepLine,
    VerifyLine,
    VerifyReqLine,
    render_claims_md,
    render_index_md,
    render_procedures_md,
    render_run_md,
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
        procedure_id="P-102", step_id="S3", implementation_id="I-19", executor="frontier",
        deps=["N-002"], goal_type="code_edit", inputs={"file": "src/auth/callback.py"},
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
        "NODE|N-003|RUNNING|Implement callback route|step=P-102:S3|impl=I-19|executor=frontier|deps=N-002"
        in md.splitlines()
    )


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


def test_unbound_implementation_renders_literal_dash_not_fabricated():
    run, node = _run_and_node(implementation_id=None)
    md = render_run_md(run, [node])
    assert "impl=-" in [ln for ln in md.splitlines() if ln.startswith("NODE|")][0]


# ===========================================================================
# index.md
# ===========================================================================


def test_index_md_exact_grammar():
    md = render_index_md(
        repo="StealthLab", revision=184, active_run="R-82",
        claim_groups=[GroupLine("generated-code", ["C-022", "C-037"])],
        procedure_groups=[GroupLine("generated-code", ["P-031"])],
        implementation_groups=[GroupLine("verification", ["I-19"])],
        run_states=[RunStateLine("RUNNING", ["N-003"]), RunStateLine("BLOCKED", [])],
    )
    lines = md.splitlines()
    assert "REPO|StealthLab" in lines
    assert "REVISION|184" in lines
    assert "ACTIVE_RUN|R-82" in lines
    assert "CLAIM_GROUP|generated-code|C-022 C-037" in lines
    assert "PROCEDURE_GROUP|generated-code|P-031" in lines
    assert "IMPLEMENTATION_GROUP|verification|I-19" in lines
    assert "RUN_STATE|RUNNING|N-003" in lines
    assert "RUN_STATE|BLOCKED|-" in lines


def test_index_md_no_active_run_renders_literal_none_not_fabricated():
    md = render_index_md(
        repo="R", revision=1, active_run=None,
        claim_groups=[], procedure_groups=[], implementation_groups=[], run_states=[],
    )
    assert "ACTIVE_RUN|none" in md.splitlines()


def test_grep_claim_group_by_topic():
    md = render_index_md(
        repo="R", revision=1, active_run=None,
        claim_groups=[GroupLine("auth", ["C-018"]), GroupLine("generated-code", ["C-022", "C-037"])],
        procedure_groups=[], implementation_groups=[], run_states=[],
    )
    matches = [ln for ln in md.splitlines() if re.match(r"^CLAIM_GROUP\|generated-code\|", ln)]
    assert matches == ["CLAIM_GROUP|generated-code|C-022 C-037"]
