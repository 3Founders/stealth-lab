"""
Proving tests for the step-0 chat-message normalizer and its two gates.

Offline by construction: `DATABASE_URL` unset, hand-rolled fakes, no network.
The fixture is a hand-written reduction of a REAL sampled row from
`nebius/SWE-rebench-openhands-trajectories` @ 35455389, with repository names
and file paths replaced. What is preserved is every structural quirk the real
row has, because the quirks are the load-bearing part:

- `tool_calls` is the literal string `"None"` on non-assistant messages
- `function.arguments` is a JSON *string*
- every message carries all five keys regardless of role
- assistant `content` is null on tool-call-only messages
- one `system` and one `user` message per trajectory
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.ingestion_sources import chat_messages as cm
from app.services.ingestion_sources.base import SourceArtifact

REPO_ROOT = Path(__file__).resolve().parents[2]


def _artifact(row: dict, *, license_metadata: dict | None = None) -> SourceArtifact:
    content = json.dumps(row, default=str)
    return SourceArtifact(
        source_type=cm.SOURCE_TYPE,
        uri="hf://nebius/SWE-rebench-openhands-trajectories@35455389/train/chatcmpl-test",
        content=content,
        content_hash="a" * 64,
        repository="example-org/example-repo",
        commit=cm.DATASET_REVISION,
        source_id="chatcmpl-test",
        license_metadata=license_metadata or {},
    )


# A reduction of a real row: 1 system, 1 user, 2 assistant messages with tool
# calls (one bash, one editor), the matching tool results, one reason-only
# assistant message, and a model_patch.
REAL_SHAPED_ROW = {
    "trajectory_id": "chatcmpl-test",
    "instance_id": "example-org__example-repo-1",
    "repo": "example-org/example-repo",
    "model_patch": "--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-old\n+new\n",
    "exit_status": "submit",
    "resolved": 1,
    "gen_tests_correct": 1.0,
    "pred_passes_gen_tests": 1.0,
    "tools": [{"function": {"name": "execute_bash"}}] * 5,
    "trajectory": [
        {"role": "system", "content": "You are a coding agent.", "name": None,
         "tool_calls": "None", "tool_call_id": None},
        {"role": "user", "content": "Fix the failing test in mod.py",
         "name": None, "tool_calls": "None", "tool_call_id": None},
        {"role": "assistant", "content": "Let me run the tests first.",
         "name": None, "tool_calls": "None", "tool_call_id": None},
        {"role": "assistant", "content": None, "name": None, "tool_call_id": None,
         "tool_calls": [
             {"function": {"arguments": json.dumps({"command": "pytest -q tests/"}),
                           "name": "execute_bash"},
              "id": "call-1", "type": "function"}]},
        {"role": "tool", "content": "1 failed, 2 passed", "name": "execute_bash",
         "tool_call_id": "call-1", "tool_calls": "None"},
        {"role": "assistant", "content": None, "name": None, "tool_call_id": None,
         "tool_calls": [
             {"function": {"arguments": json.dumps(
                 {"command": "str_replace_editor", "path": "/repo/mod.py",
                  "old_str": "old", "new_str": "new"}),
                 "name": "str_replace_editor"},
              "id": "call-2", "type": "function"}]},
        {"role": "tool", "content": "Edit applied successfully",
         "name": "str_replace_editor", "tool_call_id": "call-2", "tool_calls": "None"},
        {"role": "assistant", "content": "Re-running to confirm.",
         "name": None, "tool_calls": "None", "tool_call_id": None},
    ],
}


# --- schema pinning --------------------------------------------------------

def test_pinned_column_names_match_the_verified_corpus_schema():
    """Two of these names are wrong on the dataset card.

    `pred_passes_gen_tests` is plural, and `tools` / `model_patch` are absent
    from the card's field table entirely. Pinning them here means a future
    schema change fails a test instead of producing a silent zero-row run.
    """
    assert "pred_passes_gen_tests" in cm.EXPECTED_COLUMNS
    assert "pred_passes_gen_test" not in cm.EXPECTED_COLUMNS
    assert "tools" in cm.EXPECTED_COLUMNS
    assert "model_patch" in cm.EXPECTED_COLUMNS
    assert set(REAL_SHAPED_ROW) == set(cm.EXPECTED_COLUMNS)


def test_revision_is_a_pinned_full_sha():
    assert len(cm.DATASET_REVISION) == 40
    assert cm.DATASET_REVISION == "35455389ab51bf5e2306bfd436ef72d0f98bf882"


# --- normalization ---------------------------------------------------------

def test_tool_result_is_folded_into_its_callers_event():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    tool_events = [e for e in traj.events if e.tool_name == "execute_bash"]
    assert len(tool_events) == 1, "the role='tool' result must not become its own event"
    assert tool_events[0].tool_output == {
        "content": "1 failed, 2 passed", "name": "execute_bash",
    }
    assert tool_events[0].raw_event["tool_call_id"] == "call-1"


def test_string_none_tool_calls_is_not_mistaken_for_a_call():
    """Trap #1. `if msg["tool_calls"]:` is truthy for the string "None"."""
    assert cm._tool_calls_of(REAL_SHAPED_ROW["trajectory"][1]) == []
    assert cm._tool_calls_of(REAL_SHAPED_ROW["trajectory"][3]) != []


def test_string_encoded_arguments_are_parsed():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    editor = next(e for e in traj.events if e.tool_name == "str_replace_editor")
    assert editor.tool_input["path"] == "/repo/mod.py"
    assert editor.tool_input["old_str"] == "old"


def test_unparseable_arguments_do_not_lose_the_event():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    # index 5 is the assistant message carrying the str_replace_editor call
    row["trajectory"][5]["tool_calls"][0]["function"]["arguments"] = "{not json"
    traj = cm.normalize_chat_message_trajectory(_artifact(row))
    editor = next(e for e in traj.events if e.tool_name == "str_replace_editor")
    assert "_unparsed_arguments" in editor.tool_input


def test_test_command_is_promoted_from_execute_to_test():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    bash = next(e for e in traj.events if e.tool_name == "execute_bash")
    assert bash.canonical_event_type == "TEST", "pytest must give episode assembly a cut point"


def test_non_test_shell_stays_execute():
    assert cm._canonical_type("execute_bash", {"command": "ls -la"}) == "EXECUTE"


def test_unknown_tool_is_unclassified_not_guessed():
    assert cm._canonical_type("some_future_tool", {}) is None


def test_roles_map_to_the_cross_harness_vocabulary():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    by_name = {e.tool_name: e.canonical_event_type for e in traj.events}
    assert by_name["system"] == "OBSERVE"
    assert by_name["user"] == "OBSERVE"
    assert by_name["str_replace_editor"] == "WRITE"


def test_model_patch_travels_as_a_redactable_capped_event_not_metadata():
    """The writer redacts and size-caps tool_input/tool_output, and does
    neither to agent_traces.metadata. A 28 KB third-party diff must not sit
    on the uncapped path."""
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    patch_events = [e for e in traj.events if e.tool_name == cm._PATCH_TOOL_NAME]
    assert len(patch_events) == 1
    assert "old" in patch_events[0].tool_output["patch"]
    assert "model_patch" not in traj.metadata
    assert traj.metadata["model_patch_bytes"] > 0


def test_assistant_text_rides_in_raw_event_not_tool_input():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    editor = next(e for e in traj.events if e.tool_name == "str_replace_editor")
    assert "assistant_content" not in editor.tool_input, (
        "tool_input is the tool's own schema-bound arguments"
    )


def test_sequences_are_dense_and_dedup_keys_unique():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    assert [e.sequence for e in traj.events] == list(range(len(traj.events)))
    keys = [e.dedup_key for e in traj.events]
    assert len(set(keys)) == len(keys), "trace_events.dedup_key is NOT NULL UNIQUE"


def test_timestamps_are_strictly_increasing():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    stamps = [e.timestamp for e in traj.events]
    assert all(b > a for a, b in zip(stamps, stamps[1:]))


def test_dedup_prefix_does_not_collide_with_the_openhands_adapter():
    assert cm.DEDUP_PREFIX != "openhands"


def test_outcome_and_metadata_come_from_the_row():
    traj = cm.normalize_chat_message_trajectory(_artifact(REAL_SHAPED_ROW))
    assert traj.outcome == "success"
    assert traj.session_id == traj.trace_id
    assert traj.metadata["instance_id"] == "example-org__example-repo-1"
    assert traj.metadata["dataset_revision"] == cm.DATASET_REVISION
    assert traj.metadata["scaffold_turn_cap_reached"] is False


def test_turn_cap_is_detected_as_scaffold_termination():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    row["exit_status"] = "RuntimeError: Agent reached maximum iteration. Current iteration: 100, max iteration: 100"
    traj = cm.normalize_chat_message_trajectory(_artifact(row))
    assert traj.metadata["scaffold_turn_cap_reached"] is True
    assert cm._exit_is_turn_cap(row["exit_status"])


def test_unresolved_row_is_failure():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    row["resolved"] = 0
    assert cm.normalize_chat_message_trajectory(_artifact(row)).outcome == "failure"


def test_malformed_rows_raise_rather_than_guessing():
    with pytest.raises(cm.ChatMessageMalformedTrajectory):
        cm.normalize_chat_message_trajectory(_artifact({"trajectory": "not-a-list"}))
    with pytest.raises(cm.ChatMessageMalformedTrajectory):
        cm.normalize_chat_message_trajectory(_artifact({"no": "trajectory"}))


def test_one_odd_message_does_not_abort_the_trajectory():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    row["trajectory"].insert(3, "not a dict")
    traj = cm.normalize_chat_message_trajectory(_artifact(row))
    assert traj.skipped_malformed_events == 1
    assert traj.events, "the rest of the trajectory must still normalize"


def test_event_ceiling_is_enforced_and_counted():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    original = cm.MAX_EVENTS_PER_TRAJECTORY
    try:
        cm.MAX_EVENTS_PER_TRAJECTORY = 3
        traj = cm.normalize_chat_message_trajectory(_artifact(row))
        assert len(traj.events) == 3
        assert traj.metadata["events_truncated"] is True
    finally:
        cm.MAX_EVENTS_PER_TRAJECTORY = original


def test_tools_column_is_dropped_from_stored_content_but_counted():
    row = json.loads(json.dumps(REAL_SHAPED_ROW))
    source = cm.NebiusOpenHandsTrajectorySource()
    artifact = source.artifact_for(row)
    assert "tools" not in json.loads(artifact.content)
    traj = cm.normalize_chat_message_trajectory(artifact)
    assert traj.metadata["n_declared_tools"] == 5


# --- gate 1: per-item license ---------------------------------------------

def test_license_gate_maps_real_license_names_to_spdx_and_allows_permissive():
    """`license_name` on the parent dataset is a GitHub DISPLAY NAME
    ("MIT License"), not an SPDX slug. `identify_spdx_from_text` is a
    license-TEXT header matcher and returns None for these, which would have
    made the gate reject 100% of the corpus as unmappable. The corpus-name
    mapper that already exists is reused instead."""
    from app.services.ingestion_sources.verified_solutions_hf import normalize_spdx
    from app.services.repo_license_policy import classify_spdx, identify_spdx_from_text

    for raw, expected in (("MIT License", "MIT"), ("Apache License 2.0", "Apache-2.0")):
        assert identify_spdx_from_text(raw) is None, (
            "this is why the display-name mapper is required"
        )
        spdx = normalize_spdx(raw)
        assert spdx == expected, f"{raw!r} -> {spdx!r}"
        assert classify_spdx(spdx).decision == "ALLOW"


def test_license_gate_rejects_copyleft_and_unmappable():
    from app.services.repo_license_policy import classify_spdx, identify_spdx_from_text

    gpl = identify_spdx_from_text("GNU General Public License v3.0")
    if gpl:
        assert classify_spdx(gpl).decision == "REJECT", (
            "the copyleft reject floor is not configurable"
        )
    assert identify_spdx_from_text("") is None
    assert classify_spdx(None).decision != "ALLOW"
    assert classify_spdx("NOASSERTION").decision != "ALLOW"


def test_corpus_level_cc_by_is_not_used_as_the_per_item_gate():
    """CC-BY-4.0 is not on the permissive allowlist, so gating on the card's
    license would reject 100% of the corpus. The gate is per item, joined from
    the parent dataset; the corpus license is recorded as provenance only."""
    from app.services.repo_license_policy import classify_spdx

    assert classify_spdx("CC-BY-4.0").decision != "ALLOW"
    artifact = _artifact(REAL_SHAPED_ROW, license_metadata={
        "spdx_id": "MIT", "corpus_license": "CC-BY-4.0",
    })
    traj = cm.normalize_chat_message_trajectory(artifact)
    assert traj.metadata["license"]["spdx_id"] == "MIT"
    assert traj.metadata["license"]["corpus_license"] == "CC-BY-4.0"


# --- gate 2: held-out exclusion -------------------------------------------

def test_held_out_set_loads_and_excludes_at_instance_and_repo_level():
    from app.services.ingestion_sources.held_out import load_held_out

    held = load_held_out(REPO_ROOT)
    assert len(held) > 0
    assert held.scored_repos, "repo-level exclusion is what the Illusion paper says matters"
    sample = next(iter(sorted(held.ids)))
    assert held.is_held_out(sample)
    assert held.is_held_out(held.scored_repos[0])
    assert not held.is_held_out("example-org__example-repo-1")


def test_held_out_loader_fails_closed_on_a_missing_design():
    from app.services.ingestion_sources.held_out import HeldOutUnavailable, load_held_out

    with pytest.raises(HeldOutUnavailable):
        load_held_out("/nonexistent-root-for-tests")


def test_held_out_designs_are_sha_pinned_in_the_report():
    from app.services.ingestion_sources.held_out import load_held_out

    for snapshot in load_held_out(REPO_ROOT).snapshots:
        assert len(snapshot.sha256) == 64, "a number is meaningless unless the design is pinned"


# --- enabler fix: fenced-JSON responses -----------------------------------
# Not a step-0 file, but the step-0 pilot cannot measure any knowledge yield
# without it: every extraction call returned ```json-fenced JSON and the
# strict parser rejected all of them, so the paid path produced zero knowledge
# while still spending. Hard rule 7 -- the test ships in the same change.
#
# The prompt/schema-drift pin that used to live here was DELETED DELIBERATELY
# once the drift was fixed (that was its own instruction): the prompt now
# renders `TrajectorySemanticExtraction.model_json_schema()` verbatim, so it
# cannot name a field the strict parser rejects. Its replacement is
# `test_trajectory_semantics_async_and_budget_offline.py`
# (`test_prompt_is_derived_from_the_schema_so_it_cannot_drift_again`,
# `test_every_field_name_the_prompt_shows_survives_the_strict_parser`).

def test_fenced_json_is_unwrapped_and_still_parsed_strictly():
    from app.services.trajectory_semantics import parse_extraction_response

    payload = {
        "primary_goal": {"text": "Fix the failing test", "event_indices": [1],
                         "epistemic_status": "observed"},
        "claims": [], "failure_modes": [], "recovery_patterns": [],
        "candidate_procedures": [], "uncertainties": [],
    }
    bare = json.dumps(payload)
    for text in (bare, f"```json\n{bare}\n```", f"```\n{bare}\n```"):
        parsed = parse_extraction_response(text, max_index=10)
        assert parsed.primary_goal.text == "Fix the failing test", text[:40]


def test_genuinely_invalid_json_still_raises():
    from app.services.trajectory_semantics import (
        ExtractionTransientFailure,
        parse_extraction_response,
    )

    for text in ("not json at all", "```json\nnot json\n```", '{"broken": '):
        with pytest.raises(ExtractionTransientFailure):
            parse_extraction_response(text, max_index=10)


def test_fence_stripping_does_not_hide_a_schema_mismatch():
    """Unwrapping a fence must not become a licence to accept wrong shapes."""
    from app.services.trajectory_semantics import (
        ExtractionTransientFailure,
        parse_extraction_response,
    )

    with pytest.raises(ExtractionTransientFailure):
        parse_extraction_response('```json\n{"primary_goal": "a string, not an object"}\n```',
                                  max_index=10)


# --- the pilot's own refusals ---------------------------------------------

@pytest.mark.parametrize("host,expected", [
    ("postgresql://u:p@localhost:5432/db", True),
    ("postgresql://u:p@127.0.0.1:5432/db", True),
    ("postgresql://u:p@ep-x.us-east-2.aws.neon.tech/db", False),
])
def test_pilot_only_runs_against_loopback(host, expected):
    from app.ingestion.traj_pilot_cli import _dsn_is_loopback

    assert _dsn_is_loopback(host) is expected


def test_pilot_refuses_a_production_dsn_before_touching_the_pool(monkeypatch, capsys):
    """The refusal must happen before any DB work, so a pilot can never be the
    thing that writes to production by accident."""
    import argparse

    from app.ingestion import traj_pilot_cli as cli

    monkeypatch.setenv("SHARD_DSN", "postgresql://u:p@ep-x.aws.neon.tech/neondb")
    ns = argparse.Namespace(
        shard_dsn_env="SHARD_DSN", limit=1, root=str(REPO_ROOT), semantics=0,
        include_unresolved=False, allow_missing_designs=False, out=None,
        semantics_model="",
    )
    # pool=None on purpose: returning 2 proves the guard fires before any use.
    assert asyncio.run(cli.run(None, ns)) == 2
    assert "not loopback" in capsys.readouterr().err


def test_pilot_refuses_when_the_dsn_env_var_is_absent(monkeypatch, capsys):
    import argparse

    from app.ingestion import traj_pilot_cli as cli

    monkeypatch.delenv("DEFINITELY_NOT_SET_DSN", raising=False)
    ns = argparse.Namespace(
        shard_dsn_env="DEFINITELY_NOT_SET_DSN", limit=1, root=str(REPO_ROOT),
        semantics=0, include_unresolved=False, allow_missing_designs=False,
        out=None, semantics_model="",
    )
    assert asyncio.run(cli.run(None, ns)) == 2
    assert "is not set" in capsys.readouterr().err
