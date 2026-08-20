"""
Source-agnostic evidence collection (procedure extraction, memory-
substrate map). Extraction never knows whether an episode came from a
DB-recorded session, an in-process agent run, or (later) a Claude Code
transcript -- every source normalizes to the same ProcedureEvidence
shape, and adding a fourth source is a subclass, nothing else changes.

HONEST SCOPE: `goal_text` and `outcome` are collected as CALLER-SUPPLIED
context, not derived from the database. agent_traces.intent exists in
the schema (spec.md's Intent group) but has zero real writers today
(confirmed this session while reviewing the ingestion pipeline) -- this
module does not invent a read against a column nothing populates. A
caller that has just run an episode (the HTN executor, solve_task, a
test) knows its own goal and outcome directly; that is real information,
not something worth re-deriving badly from partial trace data.

Episode boundaries are deliberately NOT general here. Per
experiments/episode_assembly/FINDINGS.md, prompt-only segmentation
over-segments badly (18% trivial episodes) and episode boundaries vs.
procedure-extractable spans are genuinely different segmentations with
no single ticket owning the second one. This module sidesteps that
research problem entirely: a session_id (or an explicit trace_id range)
IS the episode boundary here, because an agent run already has a hard
start, a hard end, and a real graded outcome -- exactly what extraction
needs and nothing more.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import asyncpg

Outcome = Literal["success", "failure"]


@dataclass
class ProcedureEvidence:
    goal_text: str
    outcome: Outcome
    observations: list[dict] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    project_id: Optional[str] = None
    episode_id: Optional[str] = None
    session_id: Optional[str] = None
    steps_used: Optional[int] = None

    def has_observations(self) -> bool:
        return bool(self.observations)


class EvidenceSource(ABC):
    """One method, same shape as ExtractionStrategy/SchedulerStrategy --
    this repo's existing pattern for pluggable behavior
    (app/execution/htn_agent.py's SchedulerStrategy is the precedent),
    not a new convention to learn."""

    @abstractmethod
    async def collect(self) -> ProcedureEvidence: ...


class SessionEvidenceSource(EvidenceSource):
    """
    Real, DB-backed collection for a session that already ran through
    the ingestion pipeline (trace_events -> observations, wired this
    session). `observations`/`tool_sequence`/`started_at` are real reads,
    scoped to `session_id`; `goal_text`/`outcome`/`project_id` are
    caller-supplied (see module docstring for why).

    Reuses the exact observation_events -> trace_events join
    get_current_working_set() already uses (local_retrieval.py) -- same
    real join, not a second implementation of it.
    """

    def __init__(
        self, pool: asyncpg.Pool, *, session_id: str, goal_text: str, outcome: Outcome,
        project_id: Optional[str] = None, episode_id: Optional[str] = None,
        steps_used: Optional[int] = None,
    ):
        self._pool = pool
        self._session_id = session_id
        self._goal_text = goal_text
        self._outcome = outcome
        self._project_id = project_id
        self._episode_id = episode_id
        self._steps_used = steps_used

    async def collect(self) -> ProcedureEvidence:
        obs_rows = await self._pool.fetch(
            """
            SELECT DISTINCT o.id, o.observation_type, o.label, o.properties, o.extracted_at
            FROM observations o
            JOIN observation_events oe ON oe.observation_id = o.id
            JOIN trace_events te ON te.id = oe.event_id
            WHERE te.session_id = $1
            ORDER BY o.extracted_at ASC
            """,
            self._session_id,
        )
        observations = [
            {
                "id": str(r["id"]), "observation_type": r["observation_type"],
                "label": r["label"], "properties": dict(r["properties"] or {}),
            }
            for r in obs_rows
        ]

        tool_rows = await self._pool.fetch(
            "SELECT tool_name FROM trace_events "
            "WHERE session_id = $1 AND tool_name IS NOT NULL "
            "ORDER BY sequence ASC",
            self._session_id,
        )
        tool_sequence = [r["tool_name"] for r in tool_rows]

        started_at = await self._pool.fetchval(
            "SELECT MIN(\"timestamp\") FROM trace_events WHERE session_id = $1",
            self._session_id,
        )

        return ProcedureEvidence(
            goal_text=self._goal_text, outcome=self._outcome,
            observations=observations, tool_sequence=tool_sequence,
            started_at=started_at, project_id=self._project_id,
            episode_id=self._episode_id, session_id=self._session_id,
            steps_used=self._steps_used,
        )


class AgentRunEvidenceSource(EvidenceSource):
    """
    Wraps an already-completed in-process run's own result -- the
    executor-side path where no DB round trip is needed at all because
    the caller already has everything in memory (e.g. ResearchHTNAgent's
    RunContext right after `.run()` returns). No I/O; `collect()` is
    async only to satisfy the shared interface.

    `observations` here follow the SAME shape SessionEvidenceSource
    produces ({"observation_type", "label", "properties"}) even though
    they were never persisted -- derive.py and the validators consume
    ProcedureEvidence uniformly and must not need to know which source
    produced it.
    """

    def __init__(
        self, *, goal_text: str, outcome: Outcome, observations: list[dict],
        tool_sequence: list[str], started_at: Optional[datetime] = None,
        project_id: Optional[str] = None, episode_id: Optional[str] = None,
        session_id: Optional[str] = None, steps_used: Optional[int] = None,
    ):
        self._evidence = ProcedureEvidence(
            goal_text=goal_text, outcome=outcome, observations=observations,
            tool_sequence=tool_sequence, started_at=started_at,
            project_id=project_id, episode_id=episode_id,
            session_id=session_id, steps_used=steps_used,
        )

    async def collect(self) -> ProcedureEvidence:
        return self._evidence
