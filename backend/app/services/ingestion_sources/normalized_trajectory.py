"""
The harness-neutral normalized trajectory model (trajectory-ingestion-
hardening task, Sec 4 "Common Trajectory Model"). Any source adapter --
OpenHands today, a future Codex/Claude Code/Cline adapter tomorrow --
produces one `NormalizedTrajectory` of `NormalizedEvent`s; exactly one
writer (`trace_worker.write_normalized_trajectory`) turns that into
`agent_traces`/`trace_events` rows. This is the seam that makes "one
ingestion pipeline, many sources" real rather than aspirational: this
module has zero OpenHands-specific (or Claude-Code-specific) knowledge.

Deliberately NOT the same thing as the Claude Code collector's own
record shape (trace_collector.append_event's `{"session_id", "sequence",
"event_type", "dedup_key", "trace_id", "project_id", "event": {...}}`) --
that shape is append-log-specific (JSONL line format, sequence assigned
by the collector at append time). This one is source-adapter-specific
(one adapter fetch -> one complete trajectory, sequence assigned by the
normalizer itself since the whole trajectory is available at once). Both
ultimately feed the same trace_events table through their own writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class NormalizedEvent:
    """One row's worth of data, shaped to write directly into
    `trace_events` (migration 88's `canonical_event_type`/`raw_event`
    columns included) without the writer needing to know anything
    source-specific.

    `canonical_event_type`: one of the cross-harness vocabulary values
    (OBSERVE/SEARCH/READ/WRITE/EXECUTE/TEST/VERIFY/FAIL/RETRY/ROLLBACK/
    COMMIT/HANDOFF/REASON) or None when a normalizer genuinely can't
    classify an entry -- never guessed at.

    `raw_event`: the full native record when `tool_input`/`tool_output`
    can't losslessly represent a source's shape. None is a legitimate
    value (a source whose native shape already fits tool_input/
    tool_output, e.g. Claude Code, never needs this).

    `timestamp`: real wall-clock time when a source provides one; a
    SYNTHETIC, strictly-increasing ordinal timestamp (adapter-assigned,
    never claimed as real elapsed time) when it doesn't -- OpenHands
    trajectory exports carry no reliable per-step timestamp. Either way
    this is what gives episode segmentation (structural, not idle-gap
    based -- see trace_worker.py's own FINDINGS-backed rejection of idle
    gaps) a real, monotonic ordering to compute start_ts/end_ts spans
    from. None only when even a synthetic ordinal can't be assigned.
    """

    sequence: int
    canonical_event_type: Optional[str]
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: Optional[dict[str, Any]]
    raw_event: dict[str, Any]
    success: Optional[bool] = None
    error: Optional[str] = None
    dedup_key: str = ""
    timestamp: Optional[datetime] = None


@dataclass
class NormalizedTrajectory:
    """Shaped to write directly into one `agent_traces` header row plus N
    `trace_events` rows -- the exact tables the Claude Code path already
    populates. `trace_id`/`session_id` are set equal by convention for a
    single-session-per-trajectory source (OpenHands); a future adapter
    with real multi-session traces can differ them."""

    trace_id: str
    session_id: str
    provider: str
    provider_version: str
    events: list[NormalizedEvent] = field(default_factory=list)
    model: Optional[str] = None
    token_usage: Optional[dict[str, Any]] = None
    cost_usd: Optional[float] = None
    outcome: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    skipped_malformed_events: int = 0
