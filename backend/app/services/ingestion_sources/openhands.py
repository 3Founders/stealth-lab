"""
OpenHands trajectory source adapter (trajectory-ingestion-hardening task,
Sec 5). Turns an exported OpenHands trajectory file into the same
`SourceArtifact` shape every adapter in this package produces
(`SourceAdapter` protocol, base.py) -- `normalize_openhands_trajectory()`
below then turns one fetched artifact into a `NormalizedTrajectory`, which
`ingestion_sources/dispatch.py` feeds through the SAME `trace_events`/
`agent_traces` write path `trace_worker.py` already uses for the Claude
Code collector. No second ingestion system: one adapter boundary, one
downstream pipeline, matching this package's existing
`{**skill_md.SOURCE_ADAPTERS, **repo_procedural.SOURCE_ADAPTERS}` convention.

OBSERVED SHAPE (across OpenHands trajectory exports; two real variants
exist and both are handled -- anything else is quarantined rather than
guessed at):

  1. A bare JSON array, one entry per step -- either an "action" record
     (`{"action": "run", "args": {...}, "id": ..., "message": ...}`) or an
     "observation" record (`{"observation": "run", "content": ...,
     "extras": {...}, "cause": <action id>}`), interleaved.

  2. A wrapping object carrying `"history"` (the same array as above) plus
     evaluation metadata -- `instance_id`, `metrics`
     (`accumulated_cost`/`accumulated_token_usage`), `metadata.llm_config`,
     and (for SWE-bench-style runs) a `report`/`test_result.report` with a
     `resolved` boolean -- the closest thing OpenHands exports to a
     definitive outcome.

An action and its resulting observation are merged into ONE normalized
event (mirroring how `trace_events` already stores one row per completed
tool call, tool_input AND tool_output together, not one row per hook
phase) when OpenHands' own `cause` field links them, or -- for the older
export format that carries no `id`/`cause` at all -- when the observation
immediately follows its action in the array. Anything left unpaired
becomes its own event with `tool_output=None`. Nothing is ever dropped:
an unrecognized entry becomes an event with `canonical_event_type=None`
and the entry itself preserved verbatim in `raw_event`, never silently
discarded.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)
from app.services.ingestion_sources.normalized_trajectory import (
    NormalizedEvent,
    NormalizedTrajectory,
)


class OpenHandsMalformedTrajectory(ValueError):
    """Raised when a whole trajectory file cannot be parsed/classified at
    all (not valid JSON, or JSON whose shape matches neither the bare-array
    nor the history-wrapper variant). The caller (dispatch.py) catches this
    and writes a `quarantined_records` row instead of raising through the
    batch -- one bad file must never abort the rest of an ingestion run."""


# ---------------------------------------------------------------- adapter

class OpenHandsTrajectorySource:
    """Every `*.json` trajectory export under a directory tree. Follows
    the same sync-only, network-only-inside-discover/fetch contract every
    other adapter here does (base.py's SourceAdapter docstring)."""

    source_type = "openhands_trajectory"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def discover(self) -> Iterator[SourceRef]:
        root_resolved = self._root.resolve()
        for p in sorted(self._root.rglob("*.json")):
            if not p.is_file():
                continue
            try:
                resolved = p.resolve()
            except (OSError, RuntimeError):
                continue
            if resolved != root_resolved and root_resolved not in resolved.parents:
                continue  # symlink/traversal escape -- same guard skill_md.py uses
            yield SourceRef(
                uri=resolved.as_uri(),
                repository=self._root.name or str(self._root),
                path=str(p.relative_to(self._root).as_posix()),
                commit=None,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        path = self._root / ref.path if ref.path else Path(ref.uri)
        content = path.read_text(encoding="utf-8")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=None,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


SOURCE_ADAPTERS: dict[str, type] = {
    "openhands_trajectory_dir": OpenHandsTrajectorySource,
}


# ------------------------------------------------------ canonical mapping

# OpenHands's own `action`/`observation` discriminator fields serialize as
# short strings (ActionType/ObservationType enum values -- "run", "read",
# "edit", "message", "finish", ... -- not the Python class names like
# "CmdRunAction"), and that is what appears in real exported trajectory
# JSON. Mapped here to the canonical cross-harness event vocabulary
# (trajectory-ingestion-hardening task Sec 4/17). Free-text on the
# trace_events side (canonical_event_type), so extending this dict never
# needs a migration.
_ACTION_CANONICAL_TYPE: dict[str, str] = {
    "run": "EXECUTE",
    "run_ipython": "EXECUTE",
    "read": "READ",
    "write": "WRITE",
    "edit": "WRITE",
    "browse": "SEARCH",
    "browse_interactive": "SEARCH",
    "finish": "HANDOFF",
    "delegate": "HANDOFF",
    "reject": "FAIL",
    "message": "OBSERVE",
    "think": "REASON",
    "change_agent_state": "OBSERVE",
    "null": "OBSERVE",
}

# Observation-type discriminator -> canonical type, used only for a
# STANDALONE observation with no paired action (rare -- most observations
# are merged into their action's event, see _pair_history below).
_OBSERVATION_CANONICAL_TYPE: dict[str, str] = {
    "run": "EXECUTE",
    "run_ipython": "EXECUTE",
    "read": "READ",
    "write": "WRITE",
    "edit": "WRITE",
    "browse": "SEARCH",
    "delegate": "HANDOFF",
    "error": "FAIL",
    "agent_state_changed": "OBSERVE",
    "message": "OBSERVE",
    "null": "OBSERVE",
}

_TEST_COMMAND_MARKERS = ("pytest", "npm test", "npm run test", "go test", "cargo test", "jest")


def _looks_like_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _TEST_COMMAND_MARKERS)


# ------------------------------------------------------------- normalize

def _detect_schema(data: Any) -> tuple[list[Any], dict[str, Any], str]:
    """Returns (history_entries, wrapper_metadata, schema_version_label).
    Raises OpenHandsMalformedTrajectory if `data` matches neither known
    shape -- the whole-file quarantine path."""
    if isinstance(data, list):
        return data, {}, "openhands_raw_history_v1"
    if isinstance(data, dict) and isinstance(data.get("history"), list):
        return data["history"], data, "openhands_eval_wrapper_v1"
    raise OpenHandsMalformedTrajectory(
        "root is neither a bare history array nor an object with a "
        "'history' array -- not a recognizable OpenHands trajectory shape"
    )


def _extract_outcome(wrapper: dict[str, Any]) -> Optional[str]:
    # SWE-bench-style eval exports carry a resolved/unresolved verdict
    # under one of a couple of real field paths seen in the wild.
    for path in (
        ("report", "resolved"),
        ("test_result", "report", "resolved"),
    ):
        node: Any = wrapper
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, bool):
            return "success" if node else "failure"
    error = wrapper.get("error")
    if error:
        return "failure"
    return None


def _extract_model_and_cost(wrapper: dict[str, Any]) -> tuple[Optional[str], Optional[dict], Optional[float]]:
    model = None
    metadata = wrapper.get("metadata")
    if isinstance(metadata, dict):
        llm_config = metadata.get("llm_config")
        if isinstance(llm_config, dict):
            model = llm_config.get("model")
        model = model or metadata.get("model")

    metrics = wrapper.get("metrics")
    if not isinstance(metrics, dict) and isinstance(metadata, dict):
        metrics = metadata.get("metrics")
    token_usage = None
    cost_usd = None
    if isinstance(metrics, dict):
        cost_usd = metrics.get("accumulated_cost")
        if isinstance(cost_usd, (int, float)):
            cost_usd = float(cost_usd)
        else:
            cost_usd = None
        usage = metrics.get("accumulated_token_usage")
        if isinstance(usage, dict):
            token_usage = usage
    return model, token_usage, cost_usd


def _action_id(entry: dict[str, Any]) -> Any:
    return entry.get("id")


def _pair_history(history: list[Any]) -> tuple[list[tuple[dict, Optional[dict]]], int]:
    """Pairs each action with the observation it caused. Prefers
    OpenHands' own `cause` field (an observation's value pointing back to
    the producing action's `id`); falls back to array-adjacency pairing
    for the older export format that carries neither field. Returns
    (pairs, skipped_malformed_count), with `pairs` in the SAME ORDER as
    `history` -- event ordering is load-bearing (sequence numbers, episode
    assembly) so pairing decisions must never reorder the trajectory, only
    fold a consumed observation into its action's slot.

    An entry that is not even a dict, or has neither "action" nor
    "observation" as a key, is counted as skipped/malformed but is NOT
    dropped from the trajectory: it is surfaced, at its original position,
    as its own raw, unclassified event.
    """
    actions_by_id: dict[Any, int] = {}
    for idx, entry in enumerate(history):
        if isinstance(entry, dict) and "action" in entry and _action_id(entry) is not None:
            actions_by_id[_action_id(entry)] = idx

    # Decide pairing (which observation index belongs to which action
    # index) WITHOUT touching order yet. Pass 1: cause-based (any array
    # position). Pass 2: adjacency, for actions cause-based pairing left
    # unresolved -- the older export format carries neither id nor cause.
    action_obs: dict[int, int] = {}       # action index -> observation index
    consumed_obs_indices: set[int] = set()

    for obs_idx, entry in enumerate(history):
        if not isinstance(entry, dict) or "observation" not in entry:
            continue
        cause = entry.get("cause")
        if cause is None or cause not in actions_by_id:
            continue
        action_idx = actions_by_id[cause]
        if action_idx in action_obs:
            continue  # that action already has a cause-paired observation
        action_obs[action_idx] = obs_idx
        consumed_obs_indices.add(obs_idx)

    for action_idx, entry in enumerate(history):
        if not isinstance(entry, dict) or "action" not in entry or action_idx in action_obs:
            continue
        next_idx = action_idx + 1
        if (
            next_idx < len(history)
            and next_idx not in consumed_obs_indices
            and isinstance(history[next_idx], dict)
            and "observation" in history[next_idx]
        ):
            action_obs[action_idx] = next_idx
            consumed_obs_indices.add(next_idx)

    # Now walk the array exactly once, in order, emitting one event per
    # position: an action position emits (action, paired observation or
    # None); an observation position already folded into its action is
    # skipped (it was already emitted); an unconsumed observation gets
    # its own standalone event at its own position; anything else is an
    # unclassified entry, counted but preserved.
    pairs: list[tuple[dict, Optional[dict]]] = []
    skipped = 0
    for idx, entry in enumerate(history):
        if isinstance(entry, dict) and "action" in entry:
            obs_idx = action_obs.get(idx)
            pairs.append((entry, history[obs_idx] if obs_idx is not None else None))
        elif isinstance(entry, dict) and "observation" in entry:
            if idx in consumed_obs_indices:
                continue  # already emitted alongside its action
            pairs.append(({}, entry))
        else:
            skipped += 1
            pairs.append(({"_unclassified": True, "_raw": entry}, None))

    return pairs, skipped


def _command_from_action(action_entry: dict[str, Any]) -> str:
    args = action_entry.get("args")
    if isinstance(args, dict):
        return str(args.get("command") or args.get("code") or "")
    return ""


def _canonical_type_for_pair(action_entry: dict, observation_entry: Optional[dict]) -> Optional[str]:
    action_type = action_entry.get("action")
    if action_type and action_type in _ACTION_CANONICAL_TYPE:
        base_type = _ACTION_CANONICAL_TYPE[action_type]
        if base_type == "EXECUTE" and _looks_like_test_command(_command_from_action(action_entry)):
            return "TEST"
        return base_type
    if observation_entry is not None:
        obs_type = observation_entry.get("observation")
        if obs_type in _OBSERVATION_CANONICAL_TYPE:
            return _OBSERVATION_CANONICAL_TYPE[obs_type]
    return None


def _success_from_observation(observation_entry: Optional[dict]) -> Optional[bool]:
    if observation_entry is None:
        return None
    extras = observation_entry.get("extras")
    if isinstance(extras, dict) and "exit_code" in extras:
        exit_code = extras.get("exit_code")
        if isinstance(exit_code, (int, float)):
            return int(exit_code) == 0
    if observation_entry.get("observation") == "error":
        return False
    if isinstance(extras, dict) and extras.get("error") is not None:
        return False
    return None


def normalize_openhands_trajectory(
    artifact: SourceArtifact,
    *,
    trace_id: Optional[str] = None,
) -> NormalizedTrajectory:
    """Pure, deterministic, no I/O, no LLM call -- mirrors
    observations.py's `extract_deterministic_observations()` discipline:
    trivially unit-testable, and this is exactly the kind of structural
    work that must not require a model call. Raises
    OpenHandsMalformedTrajectory for a whole file that can't be classified
    at all (caller quarantines it); never raises for a merely-odd
    individual history entry, which becomes an unclassified event instead.
    """
    try:
        data = json.loads(artifact.content)
    except (ValueError, TypeError) as exc:
        raise OpenHandsMalformedTrajectory(f"not valid JSON: {exc}") from exc

    history, wrapper, schema_version = _detect_schema(data)

    resolved_trace_id = (
        trace_id
        or wrapper.get("instance_id")
        or artifact.source_id
        or artifact.content_hash[:16]
    )
    resolved_trace_id = str(resolved_trace_id)

    pairs, skipped = _pair_history(history)
    model, token_usage, cost_usd = _extract_model_and_cost(wrapper)
    outcome = _extract_outcome(wrapper)

    # OpenHands exports carry no reliable per-step wall-clock time, so a
    # synthetic, strictly-increasing ordinal timestamp is assigned here --
    # never presented as real elapsed time, only used to give episode
    # segmentation a real ordering to compute start_ts/end_ts spans from
    # (trace_worker.py's structural segmentation, not an idle-gap signal).
    base_time = artifact.discovered_at or datetime.now(timezone.utc)

    events: list[NormalizedEvent] = []
    for seq, (action_entry, observation_entry) in enumerate(pairs):
        if action_entry.get("_unclassified"):
            raw = action_entry["_raw"]
            tool_name = "unknown"
            canonical_type = None
            tool_input: dict[str, Any] = {}
            tool_output = None
            success = None
            error = None
            raw_event: dict[str, Any] = raw if isinstance(raw, dict) else {"value": raw}
        else:
            tool_name = action_entry.get("action") or (
                f"observation:{observation_entry.get('observation')}"
                if observation_entry else "unknown"
            )
            canonical_type = _canonical_type_for_pair(action_entry, observation_entry)
            args = action_entry.get("args")
            tool_input = args if isinstance(args, dict) else {}
            tool_output = None
            if observation_entry is not None:
                tool_output = {
                    "content": observation_entry.get("content"),
                    "extras": observation_entry.get("extras"),
                }
            success = _success_from_observation(observation_entry)
            error = None
            if observation_entry is not None and observation_entry.get("observation") == "error":
                content = observation_entry.get("content")
                error = content if isinstance(content, str) else json.dumps(content, default=str)
            raw_event = {"action": action_entry or None, "observation": observation_entry}

        dedup_key = hashlib.sha256(
            f"openhands:{resolved_trace_id}:{seq}".encode("utf-8")
        ).hexdigest()

        events.append(NormalizedEvent(
            sequence=seq,
            canonical_event_type=canonical_type,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            raw_event=raw_event,
            success=success,
            error=error,
            dedup_key=dedup_key,
            timestamp=base_time + timedelta(microseconds=seq),
        ))

    return NormalizedTrajectory(
        trace_id=resolved_trace_id,
        session_id=resolved_trace_id,
        provider="openhands",
        provider_version=schema_version,
        events=events,
        model=model,
        token_usage=token_usage,
        cost_usd=cost_usd,
        outcome=outcome,
        metadata={
            k: v for k, v in wrapper.items()
            if k not in ("history", "metrics", "metadata")
        },
        skipped_malformed_events=skipped,
    )
