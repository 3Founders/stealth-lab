"""
Dispatches a raw trajectory source (by `source_type`) to its adapter,
normalizes what it fetches, and writes it through the SAME
`agent_traces`/`trace_events` path every source uses
(`trace_worker.write_normalized_trajectory`) -- the concrete mechanism
behind the trajectory-ingestion-hardening task's "no second ingestion
system" rule: one adapter boundary (this module), one downstream writer,
regardless of harness.

A file/entry an adapter's normalizer can't classify at all lands in
`quarantined_records` (migration 88) rather than raising through the
batch -- one bad trajectory export must never abort the rest of an
ingestion run.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from app.services.ingestion_context import (
    complete_ingestion_context,
    open_ingestion_context,
)
from app.services.ingestion_sources.openhands import (
    OpenHandsMalformedTrajectory,
    OpenHandsTrajectorySource,
    normalize_openhands_trajectory,
)
from app.services.trace_worker import write_normalized_trajectory, write_trajectory_episodes

log = logging.getLogger(__name__)

# One entry per source_type this dispatcher knows how to route. A future
# Codex/Claude-Code-export/Cline adapter adds one line here, never a
# second write path.
TRAJECTORY_ADAPTERS: dict[str, type] = {
    "openhands_trajectory_dir": OpenHandsTrajectorySource,
}


async def _quarantine(
    pool: asyncpg.Pool,
    *,
    source_type: str,
    source_uri: Optional[str],
    raw_content: str,
    reason: str,
    schema_version_detected: Optional[str] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> None:
    await pool.execute(
        "INSERT INTO quarantined_records "
        "(source_type, source_uri, raw_content, reason, schema_version_detected, "
        " owner_id, visibility, scope_type, scope_entity_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::visibility_level,$8,$9)",
        source_type, source_uri, raw_content, reason, schema_version_detected,
        owner_id, visibility, scope_type, scope_entity_id,
    )


async def ingest_openhands_trajectories(
    pool: asyncpg.Pool,
    root: str,
    *,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    actor_id: str = "openhands_adapter",
) -> dict:
    """
    Walks `root` for OpenHands trajectory exports, normalizes each, and
    writes it through `write_normalized_trajectory()`. Idempotent:
    re-running against the same directory inserts zero new `trace_events`
    rows for events already seen (dedup_key `ON CONFLICT DO NOTHING`) and
    upserts (never duplicates) the `agent_traces` header (`ON CONFLICT
    (trace_id) DO UPDATE`). One `IngestionContext` is opened per
    trajectory file, closed `completed` on success or `failed` if
    normalization raised -- real, queryable provenance for "where did
    this trajectory come from" even for the quarantined case (the context
    itself still closes `failed`; the raw content additionally lands in
    `quarantined_records` so it's inspectable without re-parsing).
    """
    adapter = OpenHandsTrajectorySource(root)
    trajectories_ingested = 0
    events_normalized = 0
    quarantined = 0
    results: list[dict] = []

    for ref in adapter.discover():
        artifact = adapter.fetch(ref)
        ingestion_context_id = await open_ingestion_context(
            pool,
            source_type=adapter.source_type,
            extractor_id="openhands_adapter",
            extractor_version="1",
            actor_id=actor_id,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            source_uri=ref.uri,
            source_hash=artifact.content_hash,
            visibility=visibility,
            owner_id=owner_id,
        )
        try:
            trajectory = normalize_openhands_trajectory(artifact)
        except OpenHandsMalformedTrajectory as exc:
            log.warning("openhands adapter: quarantining %s: %s", ref.uri, exc)
            await _quarantine(
                pool, source_type=adapter.source_type, source_uri=ref.uri,
                raw_content=artifact.content, reason=str(exc),
                owner_id=owner_id, visibility=visibility,
                scope_type=scope_type, scope_entity_id=scope_entity_id,
            )
            await complete_ingestion_context(pool, ingestion_context_id, status="rejected")
            quarantined += 1
            continue

        write_result = await write_normalized_trajectory(
            pool, trajectory, owner_id=owner_id, visibility=visibility,
        )
        # Structural episode assembly runs right after the raw write --
        # without it, an OpenHands-ingested trajectory would have real
        # trace_events but no episodes for the semantic-extraction layer
        # (trajectory_semantics.py, which operates per-episode) to ever
        # find. Segmentation itself follows segment_events_structurally()
        # (test/commit/handoff boundaries on oversized trajectories, one
        # whole episode otherwise) -- see trace_worker.py's own docstring
        # for why this is a distinct algorithm from the Claude Code
        # transcript path, not a duplicate ingestion system.
        episode_result = await write_trajectory_episodes(
            pool, session_id=trajectory.session_id, trajectory=trajectory,
            owner_id=owner_id, visibility=visibility,
        )
        await complete_ingestion_context(pool, ingestion_context_id, status="completed")
        trajectories_ingested += 1
        events_normalized += write_result["inserted"]
        results.append({
            **write_result, "ingestion_context_id": ingestion_context_id,
            "episodes": episode_result,
        })

    return {
        "trajectories_ingested": trajectories_ingested,
        "events_normalized": events_normalized,
        "objects_quarantined": quarantined,
        "results": results,
    }
