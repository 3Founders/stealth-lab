"""End-to-end offline proof (normalize -> segment) for fixture E, the long
trajectory requiring segmentation (trajectory-ingestion-hardening task,
Sec 15/20-E). No database, no network -- both stages are pure."""
from __future__ import annotations

from pathlib import Path

from app.services.ingestion_sources.base import SourceArtifact, compute_content_hash
from app.services.ingestion_sources.openhands import normalize_openhands_trajectory
from app.services.trace_worker import OVERSIZE_SUBDIVIDE_EVENTS, segment_events_structurally

FIXTURE = Path(__file__).parent / "fixtures" / "trajectories" / "openhands_long_segmented.json"


def _load_trajectory():
    content = FIXTURE.read_text(encoding="utf-8")
    artifact = SourceArtifact(
        source_type="openhands_trajectory", uri=FIXTURE.resolve().as_uri(),
        content=content, content_hash=compute_content_hash(content),
    )
    return normalize_openhands_trajectory(artifact)


def test_fixture_is_actually_oversized():
    trajectory = _load_trajectory()
    assert len(trajectory.events) > OVERSIZE_SUBDIVIDE_EVENTS


def test_raw_event_count_is_preserved_through_normalization():
    trajectory = _load_trajectory()
    assert trajectory.skipped_malformed_events == 0
    # 1 message + 130*(read+edit) + 2 test runs + 1 finish = 264
    assert len(trajectory.events) == 264


def test_long_trajectory_is_subdivided_not_truncated():
    """Task Sec 15/21: do not simply truncate long trajectories."""
    trajectory = _load_trajectory()
    episodes = segment_events_structurally(trajectory.events)
    assert len(episodes) > 1, "an oversized trajectory with real internal TEST boundaries must subdivide"
    # Every event is accounted for across the spans -- nothing truncated.
    total_covered = sum(ep.n_events for ep in episodes)
    assert total_covered == len(trajectory.events)
    assert episodes[0].start == 0
    assert episodes[-1].end == len(trajectory.events)


def test_each_segment_retains_real_event_references():
    """Task Sec 15: 'each segment must retain event references.' Every
    episode's [start, end) span indexes real positions in the real event
    list -- provable by dereferencing them."""
    trajectory = _load_trajectory()
    episodes = segment_events_structurally(trajectory.events)
    for ep in episodes:
        span_events = trajectory.events[ep.start:ep.end]
        assert len(span_events) == ep.n_events
        assert all(e.dedup_key for e in span_events)


def test_subdivision_happened_at_real_test_boundaries():
    trajectory = _load_trajectory()
    episodes = segment_events_structurally(trajectory.events)
    assert len(episodes) == 3, "two planted TEST checkpoints -> three segments"
    for ep in episodes[:-1]:
        boundary_event = trajectory.events[ep.end - 1]
        assert boundary_event.canonical_event_type == "TEST"
