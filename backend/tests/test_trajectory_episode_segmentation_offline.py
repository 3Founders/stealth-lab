"""Offline tests for structural episode segmentation of non-Claude-Code
trajectories (trajectory-ingestion-hardening task, Sec 4/15).
`segment_events_structurally` is pure -- no database, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.ingestion_sources.normalized_trajectory import NormalizedEvent
from app.services.trace_worker import (
    OVERSIZE_SUBDIVIDE_EVENTS,
    segment_events_structurally,
)

BASE = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _event(i, canonical_type="EXECUTE"):
    return NormalizedEvent(
        sequence=i, canonical_event_type=canonical_type, tool_name="run",
        tool_input={}, tool_output=None, raw_event={},
        timestamp=BASE + timedelta(microseconds=i),
    )


def test_empty_trajectory_produces_no_episodes():
    assert segment_events_structurally([]) == []


def test_small_trajectory_is_one_whole_episode():
    events = [_event(i) for i in range(5)]
    episodes = segment_events_structurally(events)
    assert len(episodes) == 1
    assert episodes[0].start == 0
    assert episodes[0].end == 5
    assert episodes[0].n_events == 5
    assert episodes[0].flags == frozenset()


def test_episode_start_and_end_ts_come_from_real_event_timestamps():
    events = [_event(i) for i in range(3)]
    episodes = segment_events_structurally(events)
    assert episodes[0].start_ts == events[0].timestamp
    assert episodes[0].end_ts == events[-1].timestamp


def test_oversized_trajectory_with_no_internal_boundary_stays_whole_and_flagged():
    """No arbitrary cut is invented -- mirrors assemble_episodes()'s own
    honest behavior for the Claude Code path."""
    events = [_event(i) for i in range(OVERSIZE_SUBDIVIDE_EVENTS + 10)]
    episodes = segment_events_structurally(events)
    assert len(episodes) == 1
    assert "oversize_unsubdivided" in episodes[0].flags


def test_oversized_trajectory_subdivides_at_internal_test_boundaries():
    n = OVERSIZE_SUBDIVIDE_EVENTS + 20
    events = [_event(i) for i in range(n)]
    # Plant TEST boundaries roughly in the middle.
    events[n // 3] = _event(n // 3, canonical_type="TEST")
    events[2 * n // 3] = _event(2 * n // 3, canonical_type="TEST")

    episodes = segment_events_structurally(events)
    assert len(episodes) == 3
    assert all("subdivided" in e.flags for e in episodes)
    # Spans are contiguous and cover the whole trajectory.
    assert episodes[0].start == 0
    assert episodes[-1].end == n
    for a, b in zip(episodes, episodes[1:]):
        assert a.end == b.start


def test_boundary_on_the_final_event_does_not_create_a_trailing_empty_span():
    """A TEST/COMMIT event AT THE VERY LAST POSITION must not open a
    zero-length trailing episode."""
    n = OVERSIZE_SUBDIVIDE_EVENTS + 5
    events = [_event(i) for i in range(n)]
    events[n // 2] = _event(n // 2, canonical_type="COMMIT")
    events[-1] = _event(n - 1, canonical_type="COMMIT")  # last event -- no cut point after it

    episodes = segment_events_structurally(events)
    assert all(e.n_events > 0 for e in episodes)
    assert episodes[-1].end == n


def test_handoff_boundary_also_subdivides():
    n = OVERSIZE_SUBDIVIDE_EVENTS + 15
    events = [_event(i) for i in range(n)]
    events[n // 2] = _event(n // 2, canonical_type="HANDOFF")
    episodes = segment_events_structurally(events)
    assert len(episodes) == 2


def test_non_subdivision_type_does_not_create_a_boundary():
    n = OVERSIZE_SUBDIVIDE_EVENTS + 15
    events = [_event(i) for i in range(n)]
    events[n // 2] = _event(n // 2, canonical_type="READ")  # not a subdivision type
    episodes = segment_events_structurally(events)
    assert len(episodes) == 1
    assert "oversize_unsubdivided" in episodes[0].flags
