"""Offline proving tests for the OpenHands trajectory adapter
(trajectory-ingestion-hardening task, Sec 5/20). Pure -- no database, no
network, no LLM call -- `normalize_openhands_trajectory()` and
`OpenHandsTrajectorySource` are both deterministic/pure or local-file-only.

Fixtures live at tests/fixtures/trajectories/: openhands_success.json (B),
openhands_failed_retry.json (C), openhands_malformed.json (D).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.ingestion_sources.base import SourceArtifact, compute_content_hash
from app.services.ingestion_sources.openhands import (
    OpenHandsMalformedTrajectory,
    OpenHandsTrajectorySource,
    normalize_openhands_trajectory,
)

FIXTURES = Path(__file__).parent / "fixtures" / "trajectories"


def _artifact_from_file(path: Path) -> SourceArtifact:
    content = path.read_text(encoding="utf-8")
    return SourceArtifact(
        source_type="openhands_trajectory",
        uri=path.resolve().as_uri(),
        content=content,
        content_hash=compute_content_hash(content),
        path=path.name,
    )


# ------------------------------------------------------------ adapter I/O

def test_discover_finds_every_json_file(tmp_path):
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text("[]", encoding="utf-8")
    (tmp_path / "not_json.txt").write_text("ignore me", encoding="utf-8")

    adapter = OpenHandsTrajectorySource(tmp_path)
    refs = list(adapter.discover())
    paths = sorted(r.path for r in refs)
    assert paths == ["a.json", "sub/b.json"]


def test_fetch_reads_real_content_and_hashes_it(tmp_path):
    (tmp_path / "t.json").write_text('{"history": []}', encoding="utf-8")
    adapter = OpenHandsTrajectorySource(tmp_path)
    ref = next(adapter.discover())
    artifact = adapter.fetch(ref)
    assert artifact.content == '{"history": []}'
    assert artifact.content_hash == compute_content_hash('{"history": []}')
    assert adapter.fingerprint(artifact) == artifact.content_hash


# --------------------------------------------------- fixture B: success

class TestSuccessfulTrajectory:
    def setup_method(self):
        self.artifact = _artifact_from_file(FIXTURES / "openhands_success.json")
        self.trajectory = normalize_openhands_trajectory(self.artifact)

    def test_raw_event_count_is_preserved(self):
        """The fixture has 10 history entries -> 6 action/observation
        pairs (message, run+run_obs, read+read_obs, edit+edit_obs,
        run+run_obs, finish) -- every entry accounted for, none dropped."""
        assert len(self.trajectory.events) == 6
        assert self.trajectory.skipped_malformed_events == 0

    def test_header_fields_mapped_from_wrapper(self):
        assert self.trajectory.trace_id == "django__django-12345"
        assert self.trajectory.provider == "openhands"
        assert self.trajectory.model == "claude-sonnet-4-6"
        assert self.trajectory.token_usage == {"prompt_tokens": 8000, "completion_tokens": 1200}
        assert self.trajectory.cost_usd == 0.1234
        assert self.trajectory.outcome == "success"

    def test_canonical_types_cover_read_write_execute_test_handoff(self):
        types = [e.canonical_event_type for e in self.trajectory.events]
        assert types == ["OBSERVE", "EXECUTE", "READ", "WRITE", "TEST", "HANDOFF"]

    def test_read_event_is_not_silently_discarded(self):
        """The exact gap the task calls out for Claude Code Read events
        must not exist for OpenHands FileReadAction either."""
        read_events = [e for e in self.trajectory.events if e.canonical_event_type == "READ"]
        assert len(read_events) == 1
        assert read_events[0].tool_input.get("path") == "src/foo.py"
        assert read_events[0].tool_output is not None

    def test_command_pairs_with_its_observation(self):
        search_events = [e for e in self.trajectory.events if e.tool_name == "run"]
        assert len(search_events) == 2  # the grep and the pytest run
        grep_event = search_events[0]
        assert grep_event.tool_input["command"] == "grep -rn Foo src/"
        assert grep_event.tool_output["content"] == "src/foo.py:12:def Foo(x):"
        assert grep_event.success is True

    def test_test_command_is_classified_test_not_execute(self):
        test_events = [e for e in self.trajectory.events if e.tool_input.get("command") == "pytest tests/test_foo.py"]
        assert len(test_events) == 1
        assert test_events[0].canonical_event_type == "TEST"
        assert test_events[0].success is True

    def test_raw_event_preserves_full_native_payload(self):
        """Nothing OpenHands-specific is lost even after normalization --
        the full action+observation dict survives in raw_event."""
        edit_event = next(e for e in self.trajectory.events if e.tool_name == "edit")
        assert edit_event.raw_event["action"]["action"] == "edit"
        assert edit_event.raw_event["observation"]["observation"] == "edit"

    def test_dedup_keys_are_stable_across_re_normalization(self):
        """Re-normalizing the exact same artifact must produce identical
        dedup_keys -- this is what makes re-ingest idempotent downstream
        (ON CONFLICT (dedup_key) DO NOTHING)."""
        second_pass = normalize_openhands_trajectory(self.artifact)
        first_keys = [e.dedup_key for e in self.trajectory.events]
        second_keys = [e.dedup_key for e in second_pass.events]
        assert first_keys == second_keys
        assert len(set(first_keys)) == len(first_keys), "dedup_keys must be unique within one trajectory"


# ------------------------------------------------- fixture C: failed/retry

class TestFailedRetryTrajectory:
    def setup_method(self):
        self.artifact = _artifact_from_file(FIXTURES / "openhands_failed_retry.json")
        self.trajectory = normalize_openhands_trajectory(self.artifact)

    def test_outcome_is_failure_not_silently_dropped(self):
        assert self.trajectory.outcome == "failure"

    def test_every_event_still_produced_despite_failure(self):
        """Task Sec 16: failure trajectories are first-class -- a failed
        run must still normalize every event, not a truncated subset."""
        assert len(self.trajectory.events) == 7  # message + 3x(run+edit) pairs... see below
        assert self.trajectory.skipped_malformed_events == 0

    def test_repeated_failing_test_runs_are_all_classified_test(self):
        test_events = [e for e in self.trajectory.events if e.canonical_event_type == "TEST"]
        assert len(test_events) == 3
        assert all(e.success is False for e in test_events)

    def test_repeated_edits_are_all_preserved_not_collapsed(self):
        """A thrashing pattern (edit -> fail -> edit -> fail) is exactly
        the kind of failure-mode evidence the semantic layer needs to see
        -- structural normalization must not collapse or dedupe distinct
        attempts."""
        edit_events = [e for e in self.trajectory.events if e.canonical_event_type == "WRITE"]
        assert len(edit_events) == 2
        assert edit_events[0].tool_input["content"] == "attempt 1"
        assert edit_events[1].tool_input["content"] == "attempt 2"


# --------------------------------------------------- fixture D: malformed

def test_unparseable_json_raises_malformed_for_the_caller_to_quarantine():
    artifact = _artifact_from_file(FIXTURES / "openhands_malformed.json")
    with pytest.raises(OpenHandsMalformedTrajectory):
        normalize_openhands_trajectory(artifact)


def test_valid_json_but_unrecognized_shape_raises_malformed():
    artifact = SourceArtifact(
        source_type="openhands_trajectory", uri="mem://x",
        content=json.dumps({"foo": "bar"}),
        content_hash=compute_content_hash(json.dumps({"foo": "bar"})),
    )
    with pytest.raises(OpenHandsMalformedTrajectory):
        normalize_openhands_trajectory(artifact)


def test_bare_array_shape_is_accepted_without_a_wrapper():
    history = [
        {"action": "run", "args": {"command": "ls"}, "id": 1},
        {"observation": "run", "content": "a.py", "extras": {"exit_code": 0}, "cause": 1},
    ]
    content = json.dumps(history)
    artifact = SourceArtifact(
        source_type="openhands_trajectory", uri="mem://bare",
        content=content, content_hash=compute_content_hash(content),
    )
    trajectory = normalize_openhands_trajectory(artifact)
    assert len(trajectory.events) == 1
    assert trajectory.provider_version == "openhands_raw_history_v1"


def test_unclassifiable_entry_inside_an_otherwise_good_file_is_not_dropped():
    """A single odd entry must not crash the whole trajectory -- it
    becomes an unclassified event (canonical_event_type=None, raw payload
    preserved) and is counted, never silently discarded."""
    history = [
        {"action": "run", "args": {"command": "ls"}, "id": 1},
        {"observation": "run", "content": "a.py", "extras": {"exit_code": 0}, "cause": 1},
        {"something_unrecognized": True, "payload": [1, 2, 3]},
    ]
    content = json.dumps(history)
    artifact = SourceArtifact(
        source_type="openhands_trajectory", uri="mem://odd",
        content=content, content_hash=compute_content_hash(content),
    )
    trajectory = normalize_openhands_trajectory(artifact)
    assert len(trajectory.events) == 2
    assert trajectory.skipped_malformed_events == 1
    odd_event = trajectory.events[-1]
    assert odd_event.canonical_event_type is None
    assert odd_event.raw_event["something_unrecognized"] is True


# --------------------------------------------------------- adjacency mode

def test_older_format_without_ids_pairs_by_adjacency():
    """Real older OpenHands exports carry neither `id` nor `cause` --
    pairing must still work via array order."""
    history = [
        {"action": "read", "args": {"path": "x.py"}},
        {"observation": "read", "content": "print(1)\n"},
    ]
    content = json.dumps(history)
    artifact = SourceArtifact(
        source_type="openhands_trajectory", uri="mem://adjacency",
        content=content, content_hash=compute_content_hash(content),
    )
    trajectory = normalize_openhands_trajectory(artifact)
    assert len(trajectory.events) == 1
    assert trajectory.events[0].canonical_event_type == "READ"
    assert trajectory.events[0].tool_output["content"] == "print(1)\n"
