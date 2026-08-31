"""
Phase 12 (product spec: personal procedure learning loop) offline suite.
Needs NO DATABASE_URL and no live Postgres -- LocalProcedureStore is
SQLite-backed, one temp file per test, same convention as
test_local_procedure_store_offline.py.

What is REAL here: LocalProcedureStore (real sqlite3 file),
local_learning.maybe_capture_local_candidate and
describe_candidate_for_confirmation exercised against real objects, no
mocks. Nothing here touches app.local_agent.runner -- that wiring is a
deliberate, separate follow-up (see local_learning.py's own docstring).
"""
from __future__ import annotations

from app.local_agent.local_learning import (
    SUCCESS_BAR_DESCRIPTION,
    describe_candidate_for_confirmation,
    maybe_capture_local_candidate,
)
from app.local_agent.local_store import LocalProcedureStore

TASK = "Fix AttributeError from pandas DataFrame.append() removal"
NODE_NOTES = [
    "step 0 (locate call sites using df.append): stop_reason=finished, tool_calls=3",
    "step 1 (replace with pd.concat): stop_reason=finished, tool_calls=2",
]
FILES_EDITED = ["app/etl/transform.py"]
PATCH = "--- a/app/etl/transform.py\n+++ b/app/etl/transform.py\n@@ -1 +1 @@\n-df.append(x)\n+pd.concat([df, x])\n"


def _store(tmp_path) -> LocalProcedureStore:
    return LocalProcedureStore(db_path=str(tmp_path / "local_procedures.db"))


# ---------------------------------------------------------------------------
# maybe_capture_local_candidate -- success path
# ---------------------------------------------------------------------------

def test_successful_run_with_real_patch_produces_real_candidate(tmp_path):
    store = _store(tmp_path)

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
    )

    assert captured is not None
    assert captured["id"] and captured["procedure_id"]

    row = store.get_local_procedure(captured["id"])
    assert row is not None
    assert row["goal"] == TASK
    assert row["provenance"] == "system_pending_review"
    assert row["scope_type"] == "repository"
    assert row["scope_entity_id"] == str(tmp_path)
    assert row["verification_state"] == "candidate"
    assert row["staleness"] == "fresh"
    assert row["availability"] == "active"

    steps = row["steps"]
    assert len(steps) == len(NODE_NOTES)
    assert steps[0]["order"] == 0
    assert "locate call sites using df.append" in steps[0]["goal"]
    assert steps[0]["properties"]["raw_note"] == NODE_NOTES[0]
    assert steps[1]["order"] == 1
    assert "replace with pd.concat" in steps[1]["goal"]

    assert row["scope"]["files_edited"] == FILES_EDITED
    assert row["evidence_refs"][0]["files_edited"] == FILES_EDITED


def test_captured_steps_are_never_fabricated_beyond_node_notes(tmp_path):
    store = _store(tmp_path)
    single_note = ["step 0 (do the one real thing): stop_reason=finished, tool_calls=1"]

    captured = maybe_capture_local_candidate(
        store,
        task_description="one-step task",
        node_notes=single_note,
        files_edited=[],
        combined_patch="+real diff line\n",
        run_succeeded=True,
        repo_root=str(tmp_path),
    )

    assert captured is not None
    row = store.get_local_procedure(captured["id"])
    assert len(row["steps"]) == 1
    assert row["scope"]["files_edited"] == []


# ---------------------------------------------------------------------------
# maybe_capture_local_candidate -- gating: no candidate when bar not met
# ---------------------------------------------------------------------------

def test_failed_run_produces_no_candidate(tmp_path):
    store = _store(tmp_path)

    result = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=False,
        repo_root=str(tmp_path),
    )

    assert result is None
    assert store.list_local_procedures() == []


def test_empty_patch_produces_no_candidate_even_if_run_succeeded(tmp_path):
    store = _store(tmp_path)

    result = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch="",
        run_succeeded=True,
        repo_root=str(tmp_path),
    )

    assert result is None
    assert store.list_local_procedures() == []


def test_whitespace_only_patch_is_treated_as_empty(tmp_path):
    store = _store(tmp_path)

    result = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch="   \n\n  ",
        run_succeeded=True,
        repo_root=str(tmp_path),
    )

    assert result is None
    assert store.list_local_procedures() == []


# ---------------------------------------------------------------------------
# describe_candidate_for_confirmation -- real content, not a generic template
# ---------------------------------------------------------------------------

def test_confirmation_summary_reflects_real_run_data():
    summary = describe_candidate_for_confirmation(
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
    )

    assert TASK in summary
    assert str(len(NODE_NOTES)) in summary
    assert FILES_EDITED[0] in summary


def test_confirmation_summary_differs_for_different_runs():
    summary_a = describe_candidate_for_confirmation(
        task_description="task A", node_notes=["step 0 (x): stop_reason=finished, tool_calls=1"],
        files_edited=["a.py"], combined_patch="+x\n", run_succeeded=True,
    )
    summary_b = describe_candidate_for_confirmation(
        task_description="task B",
        node_notes=[
            "step 0 (x): stop_reason=finished, tool_calls=1",
            "step 1 (y): stop_reason=finished, tool_calls=1",
        ],
        files_edited=["b.py", "c.py"], combined_patch="+y\n", run_succeeded=True,
    )

    assert summary_a != summary_b
    assert "task A" in summary_a and "task A" not in summary_b
    assert "task B" in summary_b and "task B" not in summary_a


def test_confirmation_summary_says_no_candidate_when_bar_not_met():
    summary = describe_candidate_for_confirmation(
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=False,
    )

    assert "No candidate procedure would be saved" in summary
    assert SUCCESS_BAR_DESCRIPTION in summary


def test_confirmation_summary_says_no_candidate_for_empty_patch():
    summary = describe_candidate_for_confirmation(
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch="",
        run_succeeded=True,
    )

    assert "No candidate procedure would be saved" in summary
