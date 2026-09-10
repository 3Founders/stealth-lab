"""
MCP hardening B35: pure-logic half of the `.stealth/` projection --
atomic writes and the context.md/run.json/meta.json renderers, all
given fixture dicts directly (no database).
"""
import json
import os

from app.execution.stealth_projection import (
    _atomic_write,
    _render_context_md,
    _render_meta_json,
    _render_run_json,
)

_FAKE_CONTEXT = {
    "procedure_run_id": "run-1", "procedure_id": "proc-1", "procedure_version": 3,
    "status": "pending", "current_phase_or_node": "node:0", "objective": "do the thing",
    "required_preconditions": [
        {"subject": "project:1", "predicate": "lang", "object": "python", "status": "UNKNOWN"},
    ],
    "recommended_implementations": [
        {"implementation_id": "impl-1", "role": "primary", "source": "procedure_implementation_binding"},
    ],
    "blocking_unknowns": [{"subject": "project:1", "predicate": "lang", "object": "python"}],
    "waiting_child": None,
    "nodes": [{"node_order": 0, "status": "pending", "goal": "do the thing", "deps": []}],
    "parent_run_id": None, "root_run_id": "run-1",
}
_FAKE_PROCEDURE = {
    "name": "Do the thing", "goal": "do the thing",
    "postconditions": ["the thing is done"],
}
_FAKE_VERIFICATION = {
    "overall_state": "inconclusive",
    "criteria": [{"criterion_id": "postcondition:0", "statement": "the thing is done", "state": "inconclusive"}],
}


def test_atomic_write_creates_directories_and_content():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "nested", "file.txt")
        _atomic_write(path, "hello")
        with open(path, encoding="utf-8") as f:
            assert f.read() == "hello"


def test_atomic_write_leaves_no_temp_file_behind_on_success():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "file.txt")
        _atomic_write(path, "content")
        assert os.listdir(d) == ["file.txt"]


def test_render_context_md_contains_all_required_sections():
    md = _render_context_md(context=_FAKE_CONTEXT, procedure=_FAKE_PROCEDURE, verification=_FAKE_VERIFICATION)
    for section in ("[ROUTER]", "[LOCAL CLAIMS]", "[RELEVANT GLOBAL CLAIMS]",
                    "[SELECTED PROCEDURES]", "[RELEVANT IMPLEMENTATIONS]", "[COORDINATION]"):
        assert section in md


def test_render_context_md_names_the_unknown_precondition():
    md = _render_context_md(context=_FAKE_CONTEXT, procedure=_FAKE_PROCEDURE, verification=_FAKE_VERIFICATION)
    assert "project:1 lang python -> UNKNOWN" in md


def test_render_context_md_never_fabricates_relevant_global_claims():
    md = _render_context_md(context=_FAKE_CONTEXT, procedure=_FAKE_PROCEDURE, verification=_FAKE_VERIFICATION)
    section = md.split("[RELEVANT GLOBAL CLAIMS]")[1].split("[SELECTED PROCEDURES]")[0]
    assert "not projected" in section


def test_render_context_md_honest_when_no_implementation_recommended():
    context = dict(_FAKE_CONTEXT, recommended_implementations=[])
    md = _render_context_md(context=context, procedure=_FAKE_PROCEDURE, verification=_FAKE_VERIFICATION)
    assert "MISSING_IMPLEMENTATION, not fabricated" in md


def test_render_context_md_reports_waiting_child_in_coordination():
    context = dict(_FAKE_CONTEXT, waiting_child={"child_run_id": "run-2", "child_status": "pending"})
    md = _render_context_md(context=context, procedure=_FAKE_PROCEDURE, verification=_FAKE_VERIFICATION)
    assert "waiting on child run run-2 (status=pending)" in md


def test_render_run_json_is_json_serializable_and_carries_verification_state():
    run_row = {
        "execution_plan_id": "plan-1", "task_graph_id": "graph-1",
        "updated_at": None,
    }
    payload = _render_run_json(context=_FAKE_CONTEXT, run_row=run_row, verification=_FAKE_VERIFICATION)
    json.dumps(payload)  # must not raise
    assert payload["procedure_run_id"] == "run-1"
    assert payload["verification_state"] == "inconclusive"
    assert payload["node_states"] == [{"node_order": 0, "status": "pending", "deps": []}]
    assert payload["file_intents"] == []
    assert payload["node_owners"] == {}


def test_render_meta_json_is_json_serializable():
    run_row = {"id": "run-1", "updated_at": None}
    payload = _render_meta_json(
        run_row=run_row, workspace_root="/tmp/repo", scope_type="global",
        scope_entity_id=None, revision=12345,
    )
    json.dumps(payload)
    assert payload["projection_revision"] == 12345
    assert payload["workspace_root"] == "/tmp/repo"
    assert payload["sync_cursor"] == "run-1:0"
