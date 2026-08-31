"""
Rich episode evidence representation (V1 product spec, Phase 4). NOT YET
WIRED IN -- same convention `unified_retrieval.py`/`capability.py` use for
an additive module: nothing downstream changes until a caller switches to
it. `extract_procedure()` / `strategies.py` / `derive.py` / `schema.py` /
`evidence.py` (`AgentRunEvidenceSource`, `SessionEvidenceSource`) are
UNTOUCHED by this module and continue to run off the flat
`ProcedureEvidence` shape exactly as before.

WHY THIS EXISTS. Confirmed this session: extraction today (`evidence.py`,
`derive.py`) works off a flat `observations` list plus a run-length-
encoded `tool_sequence` histogram (`derive_step_skeleton`). That is
sufficient for the two fields extraction actually generalizes
(`capability_statement`, step phrasing) but it throws away structure a
future grounded-extraction pass will want: a declared goal distinct from
observed signals, a before/after state, which observations were
verification vs. plain action, and where retries happened. This module
builds that structure once, as a pure derived read, so a future wiring
pass is a call-site change, not a re-investigation of what's actually in
the database.

PURE AND READ-ONLY. `build_episode_evidence()` takes a real episode id and
a real pool and issues read-only SELECTs only -- no INSERT, no UPDATE, no
mutation of `episodes`/`observations`/`trace_events` anywhere in this
file. Nothing here calls `capture_procedure`, `persist_observation`, or
any other writer.

REAL OBSERVATION_TYPE VOCABULARY USED (confirmed against
`app/services/observations.py::extract_deterministic_observations` and
`db/14_observations.sql`'s own column comment -- both list exactly these
five, nothing invented here): 'file_touched', 'test_run',
'command_executed', 'commit_made', 'semantic_label'.

HOW AN EPISODE'S OBSERVATIONS ARE FOUND. `observations` carries no
`episode_id` column (confirmed: `db/14_observations.sql` links only via
the `observation_events` join table to `trace_events`). The real join
back to an episode is therefore `episodes.session_id -> trace_events.
session_id -> observation_events -> observations` -- the EXACT same join
`SessionEvidenceSource.collect()` already uses in `evidence.py`, reused
here rather than reinvented. An episode with a NULL `session_id` (real,
possible: `episode_type='document'` episodes never had trace data at all)
honestly yields zero observations, not a fabricated set.

HONESTY RULES THIS MODULE FOLLOWS THROUGHOUT (mirrors `derive.py`'s own
declared discipline -- "derived, not asserted"):
  - `declared_goal` is populated ONLY from a real, existing source
    (episode.metadata's own declared-goal-shaped keys, or
    agent_traces.intent for the episode's session) -- never fabricated
    from the observations. Confirmed same as evidence.py's own docstring:
    trace_worker.py's real `_episode_metadata()` writer does not stamp a
    goal field today, and agent_traces.intent has zero real writers
    (evidence.py's own confirmed finding, unchanged by this pass) -- so
    `declared_goal` is honestly None for the overwhelming majority of
    real episodes right now. That is a fact about the pipeline, not a bug
    in this module.
  - `initial_state`/`final_state` are labeled `approximate=True` always:
    they are the episode's own first/last observation, not a real
    state-projection snapshot. No such projection exists per-episode
    today (project_state() is project-scoped and as-of-time-scoped, not
    episode-scoped) -- claiming otherwise would be exactly the kind of
    fabrication this whole build is trying to avoid.
  - `decisions` is ALWAYS an empty list. This is a real, intentional slot
    for later work (Phase 6, per the calling agent's own framing) -- no
    real evidence source in this codebase distinguishes "a decision was
    made" from "an action was taken" today, so populating it would be
    invention, not derivation.
  - `verification` is populated only from real `test_run` observations
    and is None otherwise -- never inferred from a label's prose.
  - `environment` reuses `environment_facts.py::probe_environment` (the
    same pure, filesystem-only probe `derive.py`/`local_agent` already
    use) against a caller-supplied `repo_root`; None when no repo_root is
    given or it isn't a real directory -- never guessed from `properties`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import asyncpg

from app.services.environment_facts import EnvironmentFact, probe_environment

# The exact, closed vocabulary extract_deterministic_observations() (and
# its docstring / 14_observations.sql's column comment) actually produces
# today. Not re-derived from a live query -- a fixed constant, same
# discipline PROBE_PREDICATE_VOCABULARY uses for its own closed set, so a
# drift between this module and observations.py's real writer is a diff
# in one file, not a silent divergence.
KNOWN_OBSERVATION_TYPES: tuple[str, ...] = (
    "file_touched",
    "test_run",
    "command_executed",
    "commit_made",
    "semantic_label",
)

# observation_type values whose `properties` can carry an explicit
# pass/fail-shaped signal, reused for both `failures_retries` and
# `verification` -- same two types derive.py's own
# derive_failure_conditions() already treats as failure-bearing, kept
# identical here rather than re-invented with different rules.
_FAILURE_BEARING_TYPES = frozenset({"test_run", "command_executed"})


def _observation_failed(obs: dict) -> bool:
    """True only when THIS observation's own properties explicitly record
    a failure -- a test_run with `passed is False`, or a command_executed
    with a recorded nonzero/non-None exit_code. Everything else
    (including a test_run/command_executed with no such property at all)
    is honestly treated as not-known-to-have-failed, never guessed at."""
    obs_type = obs.get("observation_type")
    props = obs.get("properties") or {}
    if obs_type == "test_run":
        return props.get("passed") is False
    if obs_type == "command_executed":
        return props.get("exit_code") not in (None, 0)
    return False


@dataclass
class StateSnapshot:
    """A best-effort, HONEST approximation of episode state at one edge --
    never a real state-projection. `approximate` is always True on every
    instance this module produces; the field exists so a caller can never
    mistake this for `project_state()`'s real bi-temporal projection."""
    approximate: bool
    observation_id: Optional[str]
    observation_type: Optional[str]
    label: Optional[str]
    extracted_at: Optional[datetime]


@dataclass
class FailureRetryGroup:
    """One run of consecutive, same-type observations this episode's own
    properties mark as failed -- e.g. three consecutive failing test_run
    observations before a fourth one that (implicitly) succeeded. Built
    ONLY from explicit failure signals (`_observation_failed`); a
    same-type run with no recorded failure is not a retry group."""
    observation_type: str
    count: int
    observation_ids: list[str] = field(default_factory=list)


@dataclass
class EpisodeEvidence:
    """
    The rich, structured, per-episode evidence representation Phase 4
    asks for. An INTERNAL DERIVED OBJECT, per the calling agent's own
    framing -- no new database table backs this; every field is computed
    from `episodes`/`agent_traces`/`trace_events`/`observations`/
    `observation_events` rows that already exist.

    Field-by-field provenance (so a reader never has to guess which
    fields are grounded and which are honest gaps):
      declared_goal          -- episode.metadata or agent_traces.intent;
                                 None when neither carries one (see module
                                 docstring -- this is the common case today)
      observed_goal_signals  -- the episode's own first few observation
                                 labels, in order; [] if there are none
      initial_state           -- StateSnapshot of the first real
                                 observation, or None
      final_state              -- StateSnapshot of the last real
                                 observation, or None
      actions                 -- every observation, ordered, as a plain
                                 {order, observation_type, label,
                                 properties} dict -- the "what happened,
                                 in order" view
      tool_calls              -- trace_events.tool_name, ordered by
                                 sequence, exactly as SessionEvidenceSource
                                 already collects it
      observations             -- the real observation rows, UNSUMMARIZED
                                 (id, observation_type, label, properties,
                                 extracted_at, extractor_kind,
                                 extractor_name, code_version, model_id,
                                 event_ids)
      state_changes            -- file_touched/commit_made observations
                                 recast as {type, target, observation_id}
                                 deltas
      files_touched            -- file_path values from file_touched
                                 observations, deduped, order-preserving
      commands_run             -- command strings from command_executed
                                 observations only (test_run's own
                                 commands live in `tests_run`, not
                                 duplicated here)
      tests_run                -- test_run observations as
                                 {command, passed, label, observation_id}
      decisions                -- ALWAYS [] this pass (see module
                                 docstring -- a real, intentionally empty
                                 slot for later work, not a bug)
      outputs                  -- commit_made observations as
                                 {command, label, observation_id} -- the
                                 episode's own recorded deliverables
      verification              -- {test_run_count, all_passed, commands}
                                 built ONLY from real test_run
                                 observations, else None
      environment                -- probe_environment(repo_root) results
                                 (as plain dicts) when repo_root is a real
                                 directory, else None
      failures_retries          -- FailureRetryGroup list, built from
                                 explicit failure signals only
      provenance                -- {episode_id, session_id,
                                 source_event_ids, extractor_versions,
                                 observation_count} -- the episode's own
                                 real provenance chain
    """
    episode_id: str
    session_id: Optional[str]
    project_id: Optional[str]

    declared_goal: Optional[str]
    observed_goal_signals: list[str]

    initial_state: Optional[StateSnapshot]
    final_state: Optional[StateSnapshot]

    actions: list[dict]
    tool_calls: list[str]
    observations: list[dict]

    state_changes: list[dict]
    files_touched: list[str]
    commands_run: list[str]
    tests_run: list[dict]

    decisions: list[dict]
    outputs: list[dict]
    verification: Optional[dict]

    environment: Optional[list[dict]]
    failures_retries: list[FailureRetryGroup]

    provenance: dict


#: How many of an episode's own earliest observations count as its
#: "observed goal signals" -- a small, fixed window rather than the whole
#: episode, since a goal is what the episode OPENS with, not a summary of
#: everything it did (that's `actions`, a separate field). Consulted at
#: call time (module constant), same retunability discipline this
#: codebase uses elsewhere (e.g. trace_worker.py's
#: TRIVIAL_MERGE_MAX_EVENTS).
GOAL_SIGNAL_WINDOW = 3


async def build_episode_evidence(
    pool: asyncpg.Pool,
    episode_id: str,
    *,
    repo_root: Optional[str] = None,
) -> EpisodeEvidence:
    """
    Assemble the rich evidence representation for one real episode.
    Read-only: every query below is a SELECT. Raises ValueError if
    `episode_id` does not name a real row -- an evidence-builder that
    silently returned an empty-but-valid-looking object for a missing
    episode would be a worse failure mode than a loud one.
    """
    episode = await pool.fetchrow(
        "SELECT id, session_id, project_id, metadata, timestamp, start_ts, end_ts "
        "FROM episodes WHERE id = $1::uuid",
        episode_id,
    )
    if episode is None:
        raise ValueError(f"no episodes row for id={episode_id!r}")

    session_id = episode["session_id"]
    project_id = episode["project_id"]
    metadata = dict(episode["metadata"] or {})

    declared_goal = await _resolve_declared_goal(pool, metadata, session_id)

    obs_rows = await _fetch_observations(pool, session_id) if session_id else []
    tool_calls = await _fetch_tool_calls(pool, session_id) if session_id else []

    observations = [_observation_view(r) for r in obs_rows]
    actions = [
        {
            "order": i,
            "observation_type": o["observation_type"],
            "label": o["label"],
            "properties": o["properties"],
        }
        for i, o in enumerate(observations, start=1)
    ]

    observed_goal_signals = [o["label"] for o in observations[:GOAL_SIGNAL_WINDOW]]

    initial_state = _snapshot(observations[0]) if observations else None
    final_state = _snapshot(observations[-1]) if observations else None

    state_changes: list[dict] = []
    files_touched: list[str] = []
    commands_run: list[str] = []
    tests_run: list[dict] = []
    outputs: list[dict] = []
    _seen_files: set[str] = set()

    for o in observations:
        obs_type = o["observation_type"]
        props = o["properties"] or {}
        if obs_type == "file_touched":
            path = props.get("file_path")
            state_changes.append({
                "type": "file_touched", "target": path, "observation_id": o["id"],
            })
            if isinstance(path, str) and path not in _seen_files:
                _seen_files.add(path)
                files_touched.append(path)
        elif obs_type == "commit_made":
            state_changes.append({
                "type": "commit_made", "target": props.get("command"),
                "observation_id": o["id"],
            })
            outputs.append({
                "command": props.get("command"), "label": o["label"],
                "observation_id": o["id"],
            })
        elif obs_type == "command_executed":
            cmd = props.get("command")
            if isinstance(cmd, str):
                commands_run.append(cmd)
        elif obs_type == "test_run":
            tests_run.append({
                "command": props.get("command"), "passed": props.get("passed"),
                "label": o["label"], "observation_id": o["id"],
            })

    verification = _build_verification(tests_run)
    failures_retries = _derive_failures_retries(observations)
    environment = _probe_environment_view(repo_root)
    provenance = _build_provenance(episode_id, session_id, obs_rows)

    return EpisodeEvidence(
        episode_id=str(episode_id),
        session_id=session_id,
        project_id=project_id,
        declared_goal=declared_goal,
        observed_goal_signals=observed_goal_signals,
        initial_state=initial_state,
        final_state=final_state,
        actions=actions,
        tool_calls=tool_calls,
        observations=observations,
        state_changes=state_changes,
        files_touched=files_touched,
        commands_run=commands_run,
        tests_run=tests_run,
        decisions=[],
        outputs=outputs,
        verification=verification,
        environment=environment,
        failures_retries=failures_retries,
        provenance=provenance,
    )


async def _resolve_declared_goal(
    pool: asyncpg.Pool, metadata: dict, session_id: Optional[str],
) -> Optional[str]:
    """Real sources only, tried in order, first hit wins:
    (1) episode.metadata's own declared-goal-shaped keys -- covers a
        future writer that starts stamping one, without this module
        needing a schema change to pick it up;
    (2) agent_traces.intent for this episode's session -- the real
        column spec.md's Intent group defines, honestly noted elsewhere
        (evidence.py) as having zero real writers today, so this is a
        real, if usually empty, read;
    (3) None -- never fabricated from the observations themselves; a
        goal INFERRED from actions is `observed_goal_signals`'s job, a
        clearly different, honestly-labeled field."""
    for key in ("declared_goal", "goal", "intent", "user_goal"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if not session_id:
        return None
    intent = await pool.fetchval(
        "SELECT intent FROM agent_traces WHERE session_id = $1 "
        "AND intent IS NOT NULL ORDER BY started_at ASC LIMIT 1",
        session_id,
    )
    return intent


async def _fetch_observations(pool: asyncpg.Pool, session_id: str) -> list[asyncpg.Record]:
    """Same real join SessionEvidenceSource.collect() uses
    (observations -> observation_events -> trace_events, scoped by
    session_id), extended with the extractor-provenance columns and a
    per-observation event_id array this module additionally needs.
    GROUP BY o.id is safe to order by o.extracted_at directly (Postgres
    allows ordering/selecting by columns functionally dependent on a
    grouped primary key)."""
    return await pool.fetch(
        """
        SELECT o.id, o.observation_type, o.label, o.properties, o.extracted_at,
               o.extractor_kind, o.extractor_name, o.code_version, o.model_id,
               array_agg(DISTINCT oe.event_id::text) AS event_ids
        FROM observations o
        JOIN observation_events oe ON oe.observation_id = o.id
        JOIN trace_events te ON te.id = oe.event_id
        WHERE te.session_id = $1
        GROUP BY o.id
        ORDER BY o.extracted_at ASC
        """,
        session_id,
    )


async def _fetch_tool_calls(pool: asyncpg.Pool, session_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT tool_name FROM trace_events "
        "WHERE session_id = $1 AND tool_name IS NOT NULL "
        "ORDER BY sequence ASC",
        session_id,
    )
    return [r["tool_name"] for r in rows]


def _observation_view(row: asyncpg.Record) -> dict:
    return {
        "id": str(row["id"]),
        "observation_type": row["observation_type"],
        "label": row["label"],
        "properties": dict(row["properties"] or {}),
        "extracted_at": row["extracted_at"],
        "extractor_kind": row["extractor_kind"],
        "extractor_name": row["extractor_name"],
        "code_version": row["code_version"],
        "model_id": row["model_id"],
        "event_ids": sorted(eid for eid in (row["event_ids"] or []) if eid is not None),
    }


def _snapshot(obs: dict) -> StateSnapshot:
    return StateSnapshot(
        approximate=True,
        observation_id=obs["id"],
        observation_type=obs["observation_type"],
        label=obs["label"],
        extracted_at=obs["extracted_at"],
    )


def _build_verification(tests_run: list[dict]) -> Optional[dict]:
    """None when there is no real test_run observation at all -- never
    inferred from a command's prose or from a passing build elsewhere.
    `all_passed` is None (not True) when at least one test_run carries no
    `passed` property -- an honest "unknown", not an assumed pass."""
    if not tests_run:
        return None
    passed_values = [t["passed"] for t in tests_run]
    if all(p is True for p in passed_values):
        all_passed: Optional[bool] = True
    elif any(p is False for p in passed_values):
        all_passed = False
    else:
        all_passed = None
    return {
        "test_run_count": len(tests_run),
        "all_passed": all_passed,
        "commands": [t["command"] for t in tests_run if t["command"]],
    }


def _derive_failures_retries(observations: list[dict]) -> list[FailureRetryGroup]:
    """Consecutive-run grouping over the real observation sequence: a
    FailureRetryGroup starts when an observation of a failure-bearing type
    is itself marked failed, and extends while the immediately following
    observations are the SAME type and ALSO marked failed. A single
    isolated failure is still a group of size 1 -- a real failure, whether
    or not it repeated."""
    groups: list[FailureRetryGroup] = []
    current: Optional[FailureRetryGroup] = None
    for obs in observations:
        obs_type = obs["observation_type"]
        failed = obs_type in _FAILURE_BEARING_TYPES and _observation_failed(obs)
        if failed and current is not None and current.observation_type == obs_type:
            current.count += 1
            current.observation_ids.append(obs["id"])
        elif failed:
            current = FailureRetryGroup(
                observation_type=obs_type, count=1, observation_ids=[obs["id"]],
            )
            groups.append(current)
        else:
            current = None
    return groups


def _probe_environment_view(repo_root: Optional[str]) -> Optional[list[dict]]:
    if not repo_root:
        return None
    facts: list[EnvironmentFact] = probe_environment(repo_root)
    if not facts:
        return None
    return [{"predicate": f.predicate, "object": f.object} for f in facts]


def _build_provenance(
    episode_id: str, session_id: Optional[str], obs_rows: list[asyncpg.Record],
) -> dict:
    """The episode's own real provenance chain: every source trace_event
    id folded into any of its observations, plus the distinct set of
    extractor versions that produced them -- the replay-relevant facts
    `promote_observation_to_claim`'s own composite-version convention
    already establishes for a single observation, rolled up here across
    the whole episode."""
    source_event_ids: set[str] = set()
    extractor_versions: set[str] = set()
    for row in obs_rows:
        for eid in (row["event_ids"] or []):
            if eid is not None:
                source_event_ids.add(eid)
        parts = [row["extractor_name"], row["code_version"]]
        if row["model_id"]:
            parts.append(row["model_id"])
        extractor_versions.add(":".join(p for p in parts if p))
    return {
        "episode_id": str(episode_id),
        "session_id": session_id,
        "source_event_ids": sorted(source_event_ids),
        "extractor_versions": sorted(extractor_versions),
        "observation_count": len(obs_rows),
    }
