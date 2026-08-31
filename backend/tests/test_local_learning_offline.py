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

from app.execution.graph_executor import NodeResult
from app.local_agent.local_learning import (
    SUCCESS_BAR_DESCRIPTION,
    describe_candidate_for_confirmation,
    maybe_capture_local_candidate,
)
from app.local_agent.local_store import LocalProcedureStore
from app.services.environment_facts import EnvironmentFact

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


# ---------------------------------------------------------------------------
# richer data: real per-step NodeResult verification, environment context,
# and the deliberate honest-empty fields (P0: fix the personal learning loop)
# ---------------------------------------------------------------------------

NODE_RESULTS = {
    0: NodeResult(
        status="success", notes=NODE_NOTES[0],
        data={"files_edited": ["app/etl/transform.py"], "patch": "+use pd.concat\n", "tool_calls": 3},
    ),
    1: NodeResult(
        status="success", notes=NODE_NOTES[1],
        data={"files_edited": ["app/etl/transform.py"], "patch": "+finish migration\n", "tool_calls": 2},
    ),
}

ENV_FACTS = [
    EnvironmentFact(predicate="language", object="python"),
    EnvironmentFact(predicate="pandas_version", object="2.1.0"),
]


def test_richer_capture_carries_real_per_step_verification_and_files(tmp_path):
    """Before: a step was just {"order", "goal", "properties": {"raw_note"}}
    -- a flattened note string, nothing else. After: each step carries its
    OWN real stop_reason-derived verification and its OWN files/patch/
    tool_calls, not just the run's aggregate."""
    store = _store(tmp_path)

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
        node_results=NODE_RESULTS,
        environment_facts=ENV_FACTS,
    )

    assert captured is not None
    row = store.get_local_procedure(captured["id"])
    steps = row["steps"]
    assert len(steps) == 2

    step0 = steps[0]
    assert step0["properties"]["verification"]["declared_finished"] is True
    assert step0["properties"]["verification"]["produced_patch"] is True
    assert step0["properties"]["files_edited"] == ["app/etl/transform.py"]
    assert step0["properties"]["patch_present"] is True
    assert step0["properties"]["tool_calls"] == 3
    # The raw note is still kept verbatim -- nothing silently dropped.
    assert step0["properties"]["raw_note"] == NODE_NOTES[0]

    step1 = steps[1]
    assert step1["properties"]["verification"]["declared_finished"] is True
    assert step1["properties"]["tool_calls"] == 2

    # Real probed environment facts land as informational context, never
    # as a synthesized invariant expression.
    assert row["scope"]["environment"] == [
        {"predicate": "language", "object": "python"},
        {"predicate": "pandas_version", "object": "2.1.0"},
    ]

    # The declared-vs-verified distinction is real and lands on the
    # evidence ref, not collapsed into one boolean.
    ev = row["evidence_refs"][0]
    assert ev["declared_success"] is True
    assert ev["verified_success"] is True


def test_richer_capture_marks_a_step_that_did_not_declare_finished(tmp_path):
    """A step whose own NodeResult.status was "failure" (stop_reason !=
    "finished") must show declared_finished=False for THAT step, even
    when the run as a whole still cleared the success bar (e.g. a later
    step recovered) -- per-step signal, never borrowed from the
    aggregate."""
    store = _store(tmp_path)
    mixed_results = {
        0: NodeResult(status="failure", notes=NODE_NOTES[0], data={"files_edited": [], "patch": ""}),
        1: NodeResult(
            status="success", notes=NODE_NOTES[1],
            data={"files_edited": FILES_EDITED, "patch": PATCH, "tool_calls": 2},
        ),
    }

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
        node_results=mixed_results,
    )

    assert captured is not None
    row = store.get_local_procedure(captured["id"])
    steps = row["steps"]
    assert steps[0]["properties"]["verification"]["declared_finished"] is False
    assert steps[0]["properties"]["verification"]["produced_patch"] is False
    assert steps[1]["properties"]["verification"]["declared_finished"] is True

    # The aggregate run-level signal (declared_success) reflects that NOT
    # every step declared finished -- honest, not smoothed over.
    ev = row["evidence_refs"][0]
    assert ev["declared_success"] is False
    assert ev["verified_success"] is True


def test_richer_capture_gate_still_refuses_failed_run_with_node_results(tmp_path):
    """Richer per-step data must never weaken the evidence-sufficiency
    gate -- a run with the SAME failure signal (run_succeeded=False)
    still produces nothing, node_results or not."""
    store = _store(tmp_path)

    result = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=False,
        repo_root=str(tmp_path),
        node_results=NODE_RESULTS,
        environment_facts=ENV_FACTS,
    )

    assert result is None
    assert store.list_local_procedures() == []


def test_richer_capture_omits_node_results_and_environment_without_error(tmp_path):
    """The pre-existing call shape (no node_results/environment_facts)
    must keep working: per-step verification honestly defaults to "no
    richer signal available" rather than raising or fabricating."""
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
    row = store.get_local_procedure(captured["id"])
    assert row["steps"][0]["properties"]["verification"]["declared_finished"] is False
    assert row["steps"][0]["properties"]["files_edited"] == []
    assert "environment" not in row["scope"]


def test_capability_statement_absent_without_a_client(tmp_path):
    """No client supplied -- no LLM call is possible, so
    scope["capability_statement"] must not exist at all, not a
    placeholder string."""
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

    row = store.get_local_procedure(captured["id"])
    assert "capability_statement" not in row["scope"]


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    """Real precedent: mirrors skill_ingestion.py's own OpenAI-shaped fake
    client convention (`.chat.completions.create(...)`), no network."""
    def __init__(self, content):
        self.chat = _FakeChat(content)


def test_capability_statement_populated_when_client_provides_one(tmp_path):
    store = _store(tmp_path)
    client = _FakeClient("CAPABILITY: migrate a deprecated DataFrame method to its replacement")

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
        client=client,
    )

    row = store.get_local_procedure(captured["id"])
    assert row["scope"]["capability_statement"] == (
        "migrate a deprecated DataFrame method to its replacement"
    )


def test_capability_statement_absent_when_client_abstains(tmp_path):
    store = _store(tmp_path)
    client = _FakeClient("ABSTAIN")

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
        client=client,
    )

    row = store.get_local_procedure(captured["id"])
    assert "capability_statement" not in row["scope"]


def test_capability_statement_absent_when_client_raises(tmp_path):
    """A model call's own failure must degrade to "no statement", never
    raise out of maybe_capture_local_candidate and never fabricate one."""
    store = _store(tmp_path)

    class _RaisingClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("network down")

    captured = maybe_capture_local_candidate(
        store,
        task_description=TASK,
        node_notes=NODE_NOTES,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        repo_root=str(tmp_path),
        client=_RaisingClient(),
    )

    assert captured is not None
    row = store.get_local_procedure(captured["id"])
    assert "capability_statement" not in row["scope"]


# ---------------------------------------------------------------------------
# local_episode_evidence.build_local_episode_evidence -- honest gaps stay
# honestly empty (mirrors episode_evidence.py's own decisions=[] discipline)
# ---------------------------------------------------------------------------

def test_local_episode_evidence_never_fabricates_commands_tests_or_invariants():
    from app.local_agent.local_episode_evidence import build_local_episode_evidence

    evidence = build_local_episode_evidence(
        task_description=TASK,
        node_notes=NODE_NOTES,
        node_results=NODE_RESULTS,
        files_edited=FILES_EDITED,
        combined_patch=PATCH,
        run_succeeded=True,
        environment_facts=ENV_FACTS,
    )

    # No real evidence source backs these locally -- they must stay
    # honestly empty, not approximated from tool_calls or step counts.
    assert evidence.commands_run == []
    assert evidence.tests_run == []
    # Never synthesized from a single run's observed environment values.
    assert evidence.invariants == []
    # But the real facts themselves are still recorded, as informational
    # context, not thrown away.
    assert evidence.environment == [
        {"predicate": "language", "object": "python"},
        {"predicate": "pandas_version", "object": "2.1.0"},
    ]
    assert evidence.verification == {
        "declared_success": True,
        "verified_success": True,
        "combined_patch_present": True,
    }
