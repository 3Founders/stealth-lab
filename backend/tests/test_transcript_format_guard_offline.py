"""The wrong-format guard: structureless input must fail loudly, not
assemble a fake single episode.

CONTEXT: ingest_transcripts.py's own docstring names this hazard --
pointing a transcript reader at the hook collector directory (or any other
JSONL) "parses without error and yields silent garbage -- every line
classifying to an empty _Line, so one undifferentiated episode". Nothing
caught it, so the wrong path produced a confident single episode with a
real fingerprint and a real content_ref, and write_session_episodes()
persisted it.

Why silence is worse than a crash here: the fake episode is IDEMPOTENT.
Its fingerprint matches on re-run, so a later corrected run against the
right directory would silently insert nothing and look like a no-op.

Fully offline: assemble_episodes is a pure function -- no pool, no
network, no clock dependency beyond the timestamps in the fixtures.
"""
from __future__ import annotations

import json

import pytest

from app.services.trace_worker import (
    TranscriptFormatError,
    assemble_episodes,
)


# ---------------------------------------------------------------- fixtures

def _prompt(uuid="u1", ts="2026-08-28T10:00:00.000Z"):
    """A real user prompt line -- the structural signal every genuine
    Claude Code session opens with."""
    return {
        "uuid": uuid,
        "timestamp": ts,
        "type": "user",
        "message": {"role": "user", "content": "please fix the failing test"},
    }


def _assistant(uuid="a1", ts="2026-08-28T10:00:05.000Z"):
    return {
        "uuid": uuid,
        "timestamp": ts,
        "type": "assistant",
        "message": {"role": "assistant", "content": "on it"},
    }


def _collector_envelope(seq=0):
    """A hook collector record from .claude/traces/ -- the WRONG format.
    Parses as JSON perfectly; carries none of _classify()'s signals."""
    return {
        "dedup_key": f"sess-abc:{seq}",
        "session_id": "sess-abc",
        "sequence": seq,
        "event_type": "PreToolUse",
        "event": {"tool_name": "Read", "timestamp": "2026-08-28T10:00:00.000Z"},
    }


# ------------------------------------------------------------- the guard

def test_collector_envelopes_raise_instead_of_faking_one_episode():
    """THE regression this guard exists for: the exact wrong-directory
    mistake the docstring warned about."""
    lines = [_collector_envelope(i) for i in range(25)]
    with pytest.raises(TranscriptFormatError) as exc:
        assemble_episodes(lines)
    msg = str(exc.value)
    assert "25 record(s) parsed" in msg
    assert "wrong path" in msg or "format mismatch" in msg
    # The message must tell the operator which two directories they mixed up.
    assert ".claude/traces/" in msg
    assert "~/.claude/projects/" in msg


def test_guard_does_not_fire_on_a_real_transcript():
    """A genuine session has a prompt, so the guard must stay out of the way."""
    assembly = assemble_episodes([_prompt(), _assistant()])
    assert len(assembly.episodes) >= 1


def test_guard_is_a_valueerror_subclass():
    """Existing `except ValueError` callers keep working."""
    assert issubclass(TranscriptFormatError, ValueError)


def test_empty_file_still_returns_empty_not_raise():
    """Zero records is a legitimate terminal state (empty/new session file),
    distinct from 'records present but structureless'."""
    assembly = assemble_episodes([])
    assert assembly.episodes == []


def test_escape_hatch_is_opt_in():
    """allow_unclassified=True must restore the old behaviour exactly, so
    the guard can never become an unavoidable blocker."""
    lines = [_collector_envelope(i) for i in range(25)]
    assembly = assemble_episodes(lines, allow_unclassified=True)
    assert len(assembly.episodes) >= 1, "opt-out returns the pre-guard result"


@pytest.mark.parametrize(
    "signal_line",
    [
        _prompt(),
        {"uuid": "c1", "timestamp": "2026-08-28T10:00:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Bash", "input": {"command": "git commit -m x"}}]}},
        {"uuid": "t1", "timestamp": "2026-08-28T10:00:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests -q"}}]}},
    ],
    ids=["prompt", "commit", "test-run"],
)
def test_any_single_structural_signal_is_enough(signal_line):
    """The guard deliberately requires ALL FOUR signals absent. One real
    signal anywhere in the file must clear it, so an unusual-but-genuine
    session cannot be blocked on a single axis."""
    lines = [_collector_envelope(i) for i in range(20)] + [signal_line]
    assembly = assemble_episodes(lines)  # must not raise
    assert assembly.main_lines == 21


def test_guard_message_names_the_override():
    """An operator who genuinely needs to proceed must be told how."""
    with pytest.raises(TranscriptFormatError, match="allow_unclassified=True"):
        assemble_episodes([_collector_envelope(0)])


def test_zero_prompt_but_real_transcript_is_flagged_not_rejected():
    """The guard must NOT swallow the existing zero_prompts contract.

    A session with no prompts is a legitimate transcript fragment (resumed
    or compacted); the segmenter already marks it with a `zero_prompts`
    flag. Only records that are not transcript-shaped AT ALL may raise --
    see test_episode_segmentation.py::test_zero_prompt_session_is_one_
    flagged_episode, which this must not break.
    """
    lines = [_assistant(), {"type": "system", "content": "x"}]
    assembly = assemble_episodes(lines)  # must not raise
    assert len(assembly.episodes) == 1
    assert "zero_prompts" in assembly.episodes[0].flags


def test_shape_alone_is_not_enough_when_records_are_envelopes():
    """Collector envelopes have event_type, not type, and no message --
    so the shape test must not accidentally let them through."""
    env = _collector_envelope(0)
    assert "type" not in env and "message" not in env
    with pytest.raises(TranscriptFormatError):
        assemble_episodes([env])
