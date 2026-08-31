"""
The LOCAL, DB-free analogue of
`app/services/procedure_extraction/episode_evidence.py::EpisodeEvidence` --
same honesty discipline (derived, never fabricated; an honest gap stays an
honest gap, never a placeholder), same shape of documentation, but built
from a local ad-hoc run's OWN in-process data
(`app.execution.graph_executor.NodeResult` per node + the run's real probed
environment) instead of Postgres rows. This module never imports asyncpg or
app.db.session, directly or transitively -- app.execution.graph_executor
and app.services.environment_facts are both pure/filesystem-only, the same
guarantee `local_agent/runner.py` already relies on.

WHY THIS EXISTS, NOT A PORT OF episode_evidence.py. The server-side module
reads observations/tool_calls/tests_run back out of a database episode
already extracted into that shape. A local ad-hoc run has no episode row
and no observation pipeline at all -- what it DOES have, uniquely, is the
real `NodeResult` the local Agent+RepoSandbox loop just produced for each
step (`status`, `data["files_edited"]`, `data["patch"]`, `data["tool_calls"]`)
plus whatever `node_notes` text `_run_local_node` appended alongside it, and
the real environment facts `LocalAgentRunner.run()` already probes before
executing. This module assembles exactly that -- nothing more.

FIELD-BY-FIELD HONESTY (mirrors episode_evidence.py's own docstring style):
  declared_goal     -- task_description, always real: unlike a DB episode
                        (whose declared_goal is usually None -- no writer
                        stamps one), a local ad-hoc run always has the
                        literal task string the caller passed in.
  steps             -- one entry per real NodeResult, in node order. Each
                        step's `goal` is the SAME string `_run_local_node`
                        put in its own note (`"step N (GOAL): ..."`) --
                        pulled back out of that note's own text, never
                        re-derived independently, so step goal and note
                        text can never silently disagree. `verification`
                        is built ONLY from that step's OWN NodeResult:
                        `declared_finished` (stop_reason == "finished",
                        i.e. `NodeResult.status == "success"`) and
                        `produced_patch` (this step's own patch is
                        non-empty) -- never inferred from the run's
                        aggregate outcome.
  files_touched     -- the run's real aggregate files_edited list (the
                        caller already dedupes/sorts this across nodes;
                        passed through verbatim, not re-derived).
  commands_run      -- ALWAYS []. NodeResult.data carries no structured
                        command list (Agent+RepoSandbox's tool calls are
                        counted, not individually recorded here) -- no
                        real evidence source backs this field locally, so
                        it stays honestly empty rather than being
                        approximated from tool_calls counts. Matches
                        episode_evidence.py's own `decisions=[]` precedent:
                        a real, intentionally empty slot, not a bug.
  tests_run         -- ALWAYS []. Same reason as commands_run: nothing in
                        NodeResult distinguishes a test invocation from any
                        other tool call. A future richer `_run_local_node`
                        that captures structured tool-call records could
                        populate this; today it cannot honestly do so.
  verification      -- {declared_success, verified_success,
                        combined_patch_present}. `verified_success` is
                        exactly the caller's own `run_succeeded` (the real
                        declared-vs-verified proxy `LocalAgentRunner.run()`
                        already computes: graph outcome == "success" AND a
                        real non-empty combined patch) -- never
                        re-derived or weakened here. `declared_success` is
                        a SEPARATE, more permissive signal (every step's
                        own stop_reason said "finished") kept distinct so
                        a reader can see the two can disagree (a run can
                        declare itself finished without producing a real
                        diff).
  environment       -- the run's own real probed EnvironmentFact list, as
                        plain {predicate, object} dicts, informational
                        only. Deliberately NEVER turned into a synthesized
                        invariant expression (e.g. "pandas_version >= X")
                        here -- see `invariants` below.
  invariants        -- ALWAYS []. A single run's probed environment is one
                        observed value, not a validated bound; turning
                        "pandas_version=2.1.0" into an expression like
                        "pandas_version >= 2.1.0" would be a real
                        inference this module has no basis to make
                        unsupervised (Rule 4/2: nothing enters storage
                        without real support). `environment` above still
                        carries the same real facts as informational
                        context -- this is a deliberate, documented scope
                        limit, not an oversight.
  evidence_ref      -- {kind: "local_ad_hoc_run", ...} -- a local run has
                        no real episode id (the local agent is DB-free by
                        design), so this is explicitly NOT an episode id;
                        it is a local, in-process run reference only,
                        carried in `capture_local_procedure`'s real
                        `evidence_refs` field, never mislabeled as
                        `source_episode_ids`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.execution.graph_executor import NodeResult
from app.services.environment_facts import EnvironmentFact


def _goal_from_note(note: str) -> str:
    """Same real extraction local_learning.py's own step-building already
    used before this module existed: pull the goal out of the note's own
    `"step N (GOAL): ..."` text; fall back to the raw note verbatim when
    the shape doesn't match rather than fabricating a goal."""
    if "(" in note and ")" in note:
        try:
            extracted = note.split("(", 1)[1].rsplit(")", 1)[0].strip()
            if extracted:
                return extracted
        except (IndexError, ValueError):
            pass
    return note


@dataclass
class LocalStep:
    """One real step, built from one real NodeResult -- never a
    fabricated or re-ordered step. `verification` is populated only from
    THIS step's own signal (see module docstring); `properties` carries
    this step's own files_edited/patch_present/tool_calls, distinct from
    the run's aggregate."""
    order: int
    goal: str
    verification: dict
    properties: dict


@dataclass
class LocalEpisodeEvidence:
    declared_goal: str
    steps: list[LocalStep]

    files_touched: list[str]
    commands_run: list[str]
    tests_run: list[dict]

    verification: dict
    environment: list[dict]
    invariants: list[dict]

    evidence_ref: dict
    provenance: dict = field(default_factory=dict)


def build_local_episode_evidence(
    *,
    task_description: str,
    node_notes: list[str],
    node_results: dict[int, NodeResult],
    files_edited: list[str],
    combined_patch: str,
    run_succeeded: bool,
    environment_facts: Optional[list[EnvironmentFact]] = None,
) -> LocalEpisodeEvidence:
    """
    Pure, synchronous, no I/O -- assembles `LocalEpisodeEvidence` from data
    the caller (`local_learning.maybe_capture_local_candidate`, called from
    `LocalAgentRunner.run()`) already has in hand from the run that just
    finished. Never calls a store, never calls an LLM, never touches a
    database.

    `run_succeeded` is taken as-is, never re-derived or weakened -- it is
    the caller's own real declared-vs-verified proxy
    (`graph_outcome == "success" and bool(combined_patch)`), the same one
    `maybe_capture_local_candidate`'s evidence-sufficiency gate uses.
    """
    steps: list[LocalStep] = []
    for i, note in enumerate(node_notes):
        result = node_results.get(i)
        data = (result.data if result is not None else {}) or {}
        step_patch = data.get("patch") or ""
        declared_finished = bool(result is not None and result.status == "success")
        steps.append(LocalStep(
            order=i,
            goal=_goal_from_note(note),
            verification={
                "declared_finished": declared_finished,
                "produced_patch": bool(step_patch.strip()),
            },
            properties={
                "raw_note": note,
                "files_edited": list(data.get("files_edited", [])),
                "patch_present": bool(step_patch.strip()),
                "tool_calls": data.get("tool_calls", 0),
            },
        ))

    declared_success = bool(steps) and all(s.verification["declared_finished"] for s in steps)

    environment = (
        [{"predicate": f.predicate, "object": f.object} for f in environment_facts]
        if environment_facts else []
    )

    return LocalEpisodeEvidence(
        declared_goal=task_description,
        steps=steps,
        files_touched=list(files_edited),
        commands_run=[],
        tests_run=[],
        verification={
            "declared_success": declared_success,
            "verified_success": bool(run_succeeded),
            "combined_patch_present": bool((combined_patch or "").strip()),
        },
        environment=environment,
        invariants=[],
        evidence_ref={
            "kind": "local_ad_hoc_run",
            "combined_patch_present": bool((combined_patch or "").strip()),
            "files_edited": list(files_edited),
        },
        provenance={
            "source": "local_ad_hoc_run",
            "node_count": len(steps),
        },
    )
