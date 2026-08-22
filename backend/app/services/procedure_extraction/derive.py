"""
The deterministic core of procedure extraction (memory-substrate map).
Everything in this file is derived, not asserted -- no model call
anywhere below. This is deliberate, not a missing feature: sorting
ExtractedProcedure's fields by whether they genuinely require
generalization shows only TWO do (capability_statement, step phrasing)
-- everything else is a real read against real state, and migration 18's
own comment on procedures.preconditions says exactly this: "structured
predicates derived from the source episode's state_before projection...
NOT hand-authored tags." An LLM-authored precondition is a hand-authored
tag with extra steps; this file is what makes that unnecessary.

THE COMPOUNDING BENEFIT, stated once here because it motivates every
function below: preconditions derived from project_state() are, BY
CONSTRUCTION, already in environment_probe.PROBE_PREDICATE_VOCABULARY --
so they cannot fail V1 (groundedness). A generative extractor defends
against inventing an unmatchable precondition with a validator that
rejects bad output; this module cannot produce one in the first place.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

import asyncpg

from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import Predicate, ProcedureStep, SlotSpec
from app.services.slot_binders import best_binder_for
from app.services.state import project_state


async def derive_preconditions(pool: asyncpg.Pool, evidence: ProcedureEvidence) -> list[Predicate]:
    """
    The state_before projection itself: every claim live for this
    episode's project as of `evidence.started_at`. Empty, honestly, if
    project_id or started_at is missing -- no fabricated precondition
    stands in for a real one.

    REAL, VERIFIED LIMITATION (confirmed against a live database, not
    assumed -- the actual behavior took two wrong hypotheses to pin
    down). This does NOT reconstruct "what was believed true at
    started_at" once the environment has since changed. project_state()
    requires BOTH t_valid <= as_of (bi-temporal existence) AND
    truth_state='IN' (current epistemic belief). Once a claim is
    superseded, the OLD value fails truth_state (flipped to 'OUT'
    globally, no time dimension of its own) for every as_of, including
    one from before the supersession -- but the NEW value fails
    t_valid<=as_of for that SAME as_of, since it didn't exist yet.
    Net effect: the predicate DISAPPEARS from the projection entirely
    once superseded, for any as_of before the new claim's own t_valid --
    not the wrong value, an absent one. Extracting from an OLD episode
    after its project's environment has since changed can therefore
    silently derive FEWER preconditions than were actually true when
    that episode ran. Not fixed here -- this function wires to
    project_state() exactly as designed; the limitation is
    project_state()'s, inherited honestly rather than hidden.
    """
    if not evidence.project_id or evidence.started_at is None:
        return []
    subject = f"project:{evidence.project_id}"
    claims = await project_state(pool, subjects=[subject], as_of=evidence.started_at)
    return [
        Predicate(subject=subject, predicate=c["predicate"], object=c["object"])
        for c in claims
        if c["predicate"] is not None
    ]


async def derive_scope(pool: asyncpg.Pool, evidence: ProcedureEvidence) -> dict:
    """
    Narrowing signal for applicability._scope_matches(), not a
    disqualifying precondition -- language is a real, grounded fact
    worth using to narrow WHERE a procedure is offered, but ticket 12's
    scope mechanism (unlike preconditions) is explicitly a soft
    narrowing layer this pass records evidence for rather than
    fabricates. Only 'language' is derived, deliberately conservative --
    inferring framework/build-tool into scope too would be real, useful,
    speculative work this pass does not attempt.
    """
    if not evidence.project_id or evidence.started_at is None:
        return {}
    subject = f"project:{evidence.project_id}"
    claims = await project_state(pool, subjects=[subject], as_of=evidence.started_at)
    for c in claims:
        if c["predicate"] == "language" and c["object"]:
            return {"language": [c["object"]]}
    return {}


@dataclass
class StepGroup:
    """One run of consecutive identical tool calls -- the compressed
    unit both DeterministicExtractor (turns this into a literal step)
    and the LLM strategy (receives this, not the raw tool log, as its
    compressed input) work from."""
    tool_name: str
    count: int


def derive_step_skeleton(evidence: ProcedureEvidence) -> list[StepGroup]:
    """
    Run-length-encodes the observed tool sequence. Deliberately NOT a
    one-step-per-tool-call list -- "Read a.py, Read b.py, Read c.py,
    Edit b.py" collapses to [(Read, 3), (Edit, 1)], which is both the
    right compression for a cheap LLM call and, on its own, already a
    real generalization step: WHICH file was read stops being part of
    the procedure at all, only the ACTION pattern survives.
    """
    if not evidence.tool_sequence:
        return []
    groups: list[StepGroup] = []
    for name in evidence.tool_sequence:
        if groups and groups[-1].tool_name == name:
            groups[-1].count += 1
        else:
            groups.append(StepGroup(tool_name=name, count=1))
    return groups


def literal_steps_from_skeleton(skeleton: list[StepGroup]) -> list[ProcedureStep]:
    """DeterministicExtractor's honest, non-generalized product: one
    step per tool-group, phrased mechanically. No LLM, no ambiguity, and
    -- because it names the literal tool, not an abstracted action --
    it deliberately reads as exactly what it is: a replay skeleton, not
    a generalized method.

    `allowed_implementations` carries the tool name STRUCTURALLY, not
    only inside the prose. That name was always here (StepGroup.tool_name)
    and was previously discarded into the f-string below -- so every real
    agent run produced a genuine tool-call sequence whose shape was lost
    the moment it was extracted. `action` is left byte-identical to what
    it was, since it is what every current reader consumes.
    """
    return [
        ProcedureStep(
            order=i,
            action=f"Call {g.tool_name}" + (f" ({g.count}x)" if g.count > 1 else ""),
            allowed_implementations=[{"type": "tool", "name": g.tool_name}],
        )
        for i, g in enumerate(skeleton, start=1)
    ]


def derive_failure_conditions(evidence: ProcedureEvidence) -> list[str]:
    """
    Real signal from the episode's OWN observations -- a command_executed
    observation whose properties record a nonzero exit, or a test_run
    observation that did not pass, becomes a stated boundary rather than
    silently ignored. Conservative: only observations that explicitly
    record failure produce a condition; this does not guess.
    """
    conditions: list[str] = []
    for obs in evidence.observations:
        props = obs.get("properties") or {}
        obs_type = obs.get("observation_type")
        if obs_type == "test_run" and props.get("passed") is False:
            conditions.append("does not apply if the target test suite was already failing "
                               "for an unrelated reason")
        if obs_type == "command_executed" and props.get("exit_code") not in (None, 0):
            conditions.append(f"does not apply if `{props.get('command', 'the command')}` "
                               f"itself is expected to fail")
    # De-duplicate, order-preserving -- several observations can produce
    # the same real condition.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in conditions:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


def derive_slots(
    evidence: ProcedureEvidence, *, repo_root: Optional[str], entry_seed_files: list[str],
) -> list[SlotSpec]:
    """
    The part that carries the learned content. For each file the
    episode's observations show was actually read/touched, find which
    registered binder's output (seeded from `entry_seed_files` -- the
    episode's own starting point, e.g. the file naming the original
    failure) best covers it, via slot_binders.best_binder_for(). This
    records WHICH structural signal this kind of work depended on,
    derived from real behavior, not asserted by a model.

    Honest degradation: without a real repo_root (e.g. evidence
    collected from a source with no filesystem access), every slot binds
    to 'literal' -- still a valid, usable procedure, just not one that
    generalizes across checkouts.
    """
    touched_files = {
        obs["properties"]["file_path"]
        for obs in evidence.observations
        if obs.get("observation_type") == "file_touched" and obs.get("properties", {}).get("file_path")
    }
    if not touched_files:
        return []

    if not repo_root or not entry_seed_files:
        return [
            SlotSpec(name=f"target_file_{i}", binder="literal", description=f"file: {f}")
            for i, f in enumerate(sorted(touched_files), start=1)
        ]

    binder_name = best_binder_for(repo_root, entry_seed_files, touched_files)
    return [
        SlotSpec(
            name="target_files", binder=binder_name,
            description=f"files this procedure edits, bound via {binder_name} "
                        f"seeded from the episode's entry point",
        )
    ]
