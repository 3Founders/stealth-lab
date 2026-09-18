"""
LLM semantic extraction over one episode's trajectory (trajectory-
ingestion-hardening task, Sec 6). This is the layer the task's spec
insists on: `deterministic_v1` (observations.py) stays the STRUCTURAL
normalization layer -- it never sees more than one trace_event at a
time and only ever produces objectively-observable telemetry
(file_touched/command_executed/test_run/...). This module is the
SEMANTIC layer: one structured pass over an episode's full raw event
stream (not the lossy deterministic summary of it), producing Goals,
candidate Procedures, Implementations, and Claims -- every one of them
citing the exact source events that support it, and every one of them
tagged OBSERVED / INFERRED / GENERALIZED rather than presented as a
flat fact.

Mirrors `procedure_extraction/strategies.py::GroundedHybridExtractor`'s
discipline in spirit (one bounded LLM call, real ExtractionTransientFailure
on any infrastructure failure, an explicit abstain path, `client` always
caller-injected) but is broader in scope -- this pass is the PRIMARY
semantic interpretation of the trajectory, not a narrow two-field
abstraction over an already-derived skeleton.

WHY NOT ROUTED THROUGH procedure_extraction's ExtractionStrategy/registry
machinery: that pipeline's V1-V6 validators (validators.py) are built
specifically for a skeleton DERIVED from real tool calls plus a narrow
LLM abstraction over it (preconditions grounded in probe_vocabulary,
slots declared against a fixed binder set) -- they assume the shape
GroundedHybridExtractor produces, not a broader, freeform, Goal-
referencing procedure. Forcing this module's richer output through those
validators unmodified would either silently fail V1/V3 for legitimate
output or require extending validators.py well beyond this task's scope.
Candidate procedures from this module are captured directly via
`procedures.capture_procedure()` -- the SAME writer, SAME
"nothing is born verified" posture (provenance='system_pending_review',
approval stays pending) -- with a lighter, purpose-fit validation instead
(non-empty steps, non-empty event_refs per step, no verbatim evidence
token leaking into the capability statement). See the final report for
this as a named, disclosed deviation from a stricter "reuse the exact
registry" reading of the plan.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, Optional

import asyncpg
from pydantic import BaseModel, Field, ValidationError

from app.services.claims import capture_claim
from app.services.goals import find_or_create_goal
from app.services.implementation_identification import (
    find_or_create_implementation_identity,
    identify_implementation,
)
from app.services.observations import _decode_json_field
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.procedures import capture_procedure

log = logging.getLogger(__name__)

EXTRACTOR_ID = "trajectory_semantic_v1"
PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"
DEFAULT_MODEL = "gemma-4-31B-it"

_MAX_EVENTS_IN_PROMPT = 200  # long trajectories are segmented upstream; this is a hard safety cap
_MAX_FIELD_CHARS = 300


# ------------------------------------------------------------- schema

EpistemicStatus = Literal["observed", "inferred", "generalized"]


class SemanticElement(BaseModel):
    """One extracted Goal/subgoal/Claim/precondition/failure_mode/
    recovery_pattern/verification_action/reusable_element. `event_indices`
    are 1-based positions into the numbered event list the prompt showed
    the model -- NOT trace_event UUIDs (an LLM cannot reliably echo a UUID
    back verbatim; an integer in a known, small range is checkable and
    the orchestrator maps it to the real event id afterward)."""

    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1)
    event_indices: list[int] = Field(min_length=1)
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class CandidateProcedureStep(BaseModel):
    model_config = {"extra": "forbid"}

    description: str = Field(min_length=1)
    subgoal_text: Optional[str] = None
    event_indices: list[int] = Field(default_factory=list)


class CandidateProcedure(BaseModel):
    model_config = {"extra": "forbid"}

    capability_statement: str = Field(min_length=1)
    steps: list[CandidateProcedureStep] = Field(min_length=1)
    event_indices: list[int] = Field(min_length=1)
    epistemic_status: EpistemicStatus = "inferred"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class CandidateImplementation(BaseModel):
    model_config = {"extra": "forbid"}

    tool_name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    event_indices: list[int] = Field(min_length=1)
    applicability_notes: Optional[str] = None


Outcome = Literal["success", "failure", "partial_success", "abandoned", "blocked", "unknown"]


class TrajectorySemanticExtraction(BaseModel):
    """The strict structured-output contract the model must fill. Every
    list may legitimately be empty (a genuine per-field abstain) --
    ONLY `uncertainties` exists specifically to hold anything the model
    is unsure how to classify, so nothing forces a fabricated element
    just to make a list non-empty."""

    model_config = {"extra": "forbid"}

    primary_goal: Optional[SemanticElement] = None
    subgoals: list[SemanticElement] = Field(default_factory=list)
    candidate_procedures: list[CandidateProcedure] = Field(default_factory=list)
    implementations: list[CandidateImplementation] = Field(default_factory=list)
    claims: list[SemanticElement] = Field(default_factory=list)
    preconditions: list[SemanticElement] = Field(default_factory=list)
    failure_modes: list[SemanticElement] = Field(default_factory=list)
    recovery_patterns: list[SemanticElement] = Field(default_factory=list)
    verification_actions: list[SemanticElement] = Field(default_factory=list)
    outcome: Optional[Outcome] = None
    reusable_elements: list[SemanticElement] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- prompt

_SYSTEM_PROMPT = """You perform semantic extraction over one agent trajectory (a bounded, \
numbered sequence of tool-call events). Output STRICT JSON matching the given schema. Nothing else.

Rules:
- Every extracted element MUST cite `event_indices`: the 1-based numbers of the events (from the \
numbered list given to you) that directly support it. Never cite an index outside the given range. \
Never leave event_indices empty for anything except `uncertainties` (which is plain text, not an \
element).
- Tag every element's `epistemic_status`:
  OBSERVED = directly present in the trajectory (e.g. "a file was read then edited").
  INFERRED = a reasonable semantic interpretation of what happened (e.g. "the agent was debugging \
a test failure").
  GENERALIZED = a candidate reusable insight that plausibly extends beyond this one run (e.g. "for \
generated files, editing the source and regenerating beats editing the generated file directly").
  Default to INFERRED when unsure; never claim OBSERVED for something not literally present.
- A `candidate_procedures[]` entry requires an ACTUAL ordered sequence of steps genuinely present in \
the trajectory -- never invent one from a single ambiguous action. If the trajectory has no coherent \
reusable procedure, leave candidate_procedures empty; do not force one.
- Each procedure step's `subgoal_text` should name the STEP'S GOAL (e.g. "locate the failing test"), \
not the literal tool call (e.g. NOT "run pytest tests/test_foo.py") -- concrete tools belong in \
`implementations`, not in the procedure step text.
- `implementations[]` identifies CONCRETE tools/mechanisms actually used (e.g. "pytest", "ripgrep") \
and their role; never invent a tool that wasn't actually called.
- `claims[]` are semantic propositions ("the test failure disappeared after regenerating the client \
from the schema"), never a restatement of raw telemetry ("a file was modified").
- Extract real value from a FAILED trajectory too: attempted goal, failure point, failed \
precondition, recovery attempts, and any repeated-action/thrashing pattern belong in \
failure_modes/recovery_patterns -- do not leave everything empty just because the run failed.
- If you are unsure how to classify something, put a short note in `uncertainties` instead of \
forcing it into a typed field with a low-quality guess.
"""


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > _MAX_FIELD_CHARS:
        return text[:_MAX_FIELD_CHARS] + "...[truncated]"
    return text


def _numbered_event_lines(events: list[dict]) -> list[str]:
    lines = []
    for i, event in enumerate(events, start=1):
        kind = event.get("canonical_event_type") or event.get("event_type") or "?"
        tool = event.get("tool_name") or ""
        tool_input = _truncate(event.get("tool_input") or {})
        tool_output = _truncate(event.get("tool_output") or {})
        success = event.get("success")
        success_str = "" if success is None else (" ok" if success else " FAILED")
        lines.append(f"{i}. [{kind}]{success_str} {tool} input={tool_input} output={tool_output}")
    return lines


def _build_user_prompt(goal_text: Optional[str], events: list[dict]) -> str:
    header = f"Declared/prior goal text (may be absent or wrong): {goal_text or '(none)'}\n"
    body = "\n".join(_numbered_event_lines(events))
    return f"{header}Events (1-{len(events)}):\n{body}"


def _input_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _output_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _validate_indices(indices: list[int], max_index: int) -> list[int]:
    """Keeps only in-range indices -- an out-of-range citation is a model
    error, not something to trust or crash on. Returns the filtered list
    (possibly empty; the caller decides whether that demotes the element
    to `uncertainties`)."""
    return [i for i in indices if 1 <= i <= max_index]


def parse_extraction_response(text: str, *, max_index: int) -> TrajectorySemanticExtraction:
    """Strict parse: valid JSON, valid against the Pydantic schema. Raises
    ExtractionTransientFailure on anything else -- same "never silently
    substitute a degraded result" discipline GroundedHybridExtractor
    uses. Out-of-range event_indices are filtered (not fatal) per element;
    an element left with zero valid indices after filtering is dropped
    and recorded in `uncertainties` instead of persisted as canonical
    knowledge with fabricated evidence."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ExtractionTransientFailure(
            f"semantic extraction response was not valid JSON: {text[:200]!r}"
        ) from exc

    try:
        parsed = TrajectorySemanticExtraction.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionTransientFailure(
            f"semantic extraction response did not match the expected schema: {exc}"
        ) from exc

    dropped: list[str] = list(parsed.uncertainties)

    def _filter_element(el: SemanticElement) -> Optional[SemanticElement]:
        kept = _validate_indices(el.event_indices, max_index)
        if not kept:
            dropped.append(f"dropped (no valid event_indices): {el.text[:80]!r}")
            return None
        if kept != el.event_indices:
            el = el.model_copy(update={"event_indices": kept})
        return el

    def _filter_list(elements: list[SemanticElement]) -> list[SemanticElement]:
        out = []
        for el in elements:
            filtered = _filter_element(el)
            if filtered is not None:
                out.append(filtered)
        return out

    parsed.subgoals = _filter_list(parsed.subgoals)
    parsed.claims = _filter_list(parsed.claims)
    parsed.preconditions = _filter_list(parsed.preconditions)
    parsed.failure_modes = _filter_list(parsed.failure_modes)
    parsed.recovery_patterns = _filter_list(parsed.recovery_patterns)
    parsed.verification_actions = _filter_list(parsed.verification_actions)
    parsed.reusable_elements = _filter_list(parsed.reusable_elements)
    if parsed.primary_goal is not None:
        parsed.primary_goal = _filter_element(parsed.primary_goal)

    kept_procedures = []
    for proc in parsed.candidate_procedures:
        proc_indices = _validate_indices(proc.event_indices, max_index)
        if not proc_indices:
            dropped.append(f"dropped candidate_procedure (no valid event_indices): {proc.capability_statement[:80]!r}")
            continue
        valid_steps = [
            step for step in proc.steps
            if not step.event_indices or _validate_indices(step.event_indices, max_index)
        ]
        if not valid_steps:
            dropped.append(f"dropped candidate_procedure (no valid steps): {proc.capability_statement[:80]!r}")
            continue
        for step in valid_steps:
            step.event_indices = _validate_indices(step.event_indices, max_index)
        kept_procedures.append(proc.model_copy(update={"event_indices": proc_indices, "steps": valid_steps}))
    parsed.candidate_procedures = kept_procedures

    kept_impls = []
    for impl in parsed.implementations:
        impl_indices = _validate_indices(impl.event_indices, max_index)
        if impl_indices:
            kept_impls.append(impl.model_copy(update={"event_indices": impl_indices}))
        else:
            dropped.append(f"dropped implementation (no valid event_indices): {impl.tool_name!r}")
    parsed.implementations = kept_impls

    parsed.uncertainties = dropped
    return parsed


# ---------------------------------------------------------- episode I/O

async def _fetch_episode_and_events(pool: asyncpg.Pool, episode_id: str) -> tuple[dict, list[dict]]:
    """Loads the episode row plus every `trace_events` row within its
    session/time span (the same time-range containment convention
    `ingestion_jobs.resolve_justification_episode()` already uses,
    inverted: there we find the episode for an event, here we find the
    events for an episode). Deliberately reads the RAW events, not the
    already-derived (and lossy) Observations -- the whole point of this
    module is that the semantic layer sees the full trajectory, not a
    telemetry summary of it."""
    episode = await pool.fetchrow(
        "SELECT id, session_id, project_id, metadata, start_ts, end_ts, owner_id, "
        "       visibility::text AS visibility, scope_type, scope_entity_id "
        "FROM episodes WHERE id = $1::uuid",
        episode_id,
    )
    if episode is None:
        raise ValueError(f"no episodes row for id={episode_id!r}")
    episode_dict = dict(episode)
    if not episode_dict.get("session_id"):
        return episode_dict, []

    rows = await pool.fetch(
        "SELECT id, sequence, event_type, canonical_event_type, tool_name, "
        "       tool_input, tool_output, success, \"timestamp\" "
        "FROM trace_events "
        "WHERE session_id = $1 "
        "  AND ($2::timestamptz IS NULL OR \"timestamp\" >= $2) "
        "  AND ($3::timestamptz IS NULL OR \"timestamp\" <= $3) "
        "ORDER BY sequence ASC",
        episode_dict["session_id"], episode_dict.get("start_ts"), episode_dict.get("end_ts"),
    )
    return episode_dict, [dict(r) for r in rows]


def _resolve_goal_text(episode: dict) -> Optional[str]:
    metadata = episode.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    if not isinstance(metadata, dict):
        return None
    for key in ("declared_goal", "goal", "intent", "user_goal"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


# -------------------------------------------------------------- orchestrator

async def extract_trajectory_semantics(
    pool: asyncpg.Pool,
    episode_id: str,
    *,
    client: Any,
    model: str = DEFAULT_MODEL,
    escalated: bool = False,
    escalation_reason: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    created_by: str = EXTRACTOR_ID,
) -> dict:
    """
    One structured semantic-extraction pass over one episode. Writes a
    `trajectory_extractions` row up front (status='pending'), so a crash
    mid-call leaves a real, inspectable, re-runnable failure record
    rather than silent nothing -- flips to 'completed'/'failed' at the
    end. Never gated on outcome=='success' (task Sec 16: failure
    trajectories are first-class) -- the only gate is "does this episode
    have any cited events at all".

    Every produced Goal/Claim/Implementation/candidate-Procedure gets a
    `trajectory_extraction_objects` row citing its real source
    `trace_events` ids -- the event_refs are the eventual proof this
    extraction did not fabricate anything.

    Raises `ExtractionTransientFailure` on any LLM/parse failure (mirrors
    `GroundedHybridExtractor`'s discipline) -- the caller decides whether
    to retry, escalate to a stronger model, or give up; nothing here
    silently degrades.

    `escalated`/`escalation_reason`: this function does not decide model
    routing itself -- the caller is expected to have already called
    `extraction_routing.choose_extraction_model()` and pass its result
    straight through (`model=choice.model, escalated=choice.escalated,
    escalation_reason=choice.escalation_reason`). Recorded on the
    `trajectory_extractions` row purely for observability (Sec 23's
    `escalation_rate` metric) -- this function's own behavior is
    identical either way.
    """
    episode, events = await _fetch_episode_and_events(pool, episode_id)
    if not events:
        raise ValueError(
            f"episode {episode_id} has no trace_events in its session/time span -- "
            "nothing to extract from"
        )

    # Hierarchical segmentation (long trajectories) is handled upstream by
    # the caller, which is expected to have already split an oversized
    # episode into child episodes before calling this function once per
    # child. This cap is a last-resort safety net, not the segmentation
    # mechanism itself.
    events = events[:_MAX_EVENTS_IN_PROMPT]
    decoded_events = [
        {
            **e,
            "tool_input": _decode_json_field(e.get("tool_input")),
            "tool_output": _decode_json_field(e.get("tool_output")),
        }
        for e in events
    ]

    resolved_scope_type = (
        scope_type or episode.get("scope_type")
        or ("project" if episode.get("project_id") else "global")
    )
    resolved_scope_entity_id = (
        scope_entity_id or episode.get("scope_entity_id") or episode.get("project_id")
    )
    resolved_owner_id = owner_id if owner_id is not None else episode.get("owner_id")
    resolved_visibility = visibility if owner_id is not None or visibility != "public" else (
        episode.get("visibility") or "public"
    )

    goal_text = _resolve_goal_text(episode)
    prompt = _build_user_prompt(goal_text, decoded_events)
    input_hash = _input_hash(prompt)

    extraction_row = await pool.fetchrow(
        "INSERT INTO trajectory_extractions "
        "(episode_id, ingestion_context_id, extractor_id, model, prompt_version, "
        " schema_version, input_hash, status, owner_id, visibility, scope_type, scope_entity_id, "
        " escalated, escalation_reason) "
        "VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,'pending',$8,$9::visibility_level,$10,$11,$12,$13) "
        "RETURNING id",
        episode_id, ingestion_context_id, EXTRACTOR_ID, model, PROMPT_VERSION,
        SCHEMA_VERSION, input_hash, resolved_owner_id, resolved_visibility,
        resolved_scope_type, resolved_scope_entity_id, escalated, escalation_reason,
    )
    extraction_id = str(extraction_row["id"])

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4000,
        )
        raw_text = response.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001 -- any transport/client failure is real
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid",
            extraction_id, repr(exc),
        )
        raise ExtractionTransientFailure(f"semantic extraction LLM call failed: {exc!r}") from exc

    try:
        extraction = parse_extraction_response(raw_text, max_index=len(decoded_events))
    except ExtractionTransientFailure as exc:
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid",
            extraction_id, str(exc),
        )
        raise

    event_id_by_index = {i: str(e["id"]) for i, e in enumerate(decoded_events, start=1)}
    counts = {"goals": 0, "claims": 0, "procedures": 0, "implementations": 0}

    async def _link(object_type: str, object_id: str, indices: list[int],
                     epistemic_status: str, confidence: Optional[float] = None) -> None:
        event_refs = [event_id_by_index[i] for i in indices if i in event_id_by_index]
        if not event_refs:
            return
        await pool.execute(
            "INSERT INTO trajectory_extraction_objects "
            "(extraction_id, object_type, object_id, event_refs, epistemic_status, confidence) "
            "VALUES ($1::uuid,$2,$3::uuid,$4::uuid[],$5,$6)",
            extraction_id, object_type, object_id, event_refs, epistemic_status, confidence,
        )

    goal_kwargs = dict(
        scope_type=resolved_scope_type,
        scope_entity_id=resolved_scope_entity_id,
        provenance="system_pending_review",
        owner_id=resolved_owner_id,
        visibility=resolved_visibility,
        created_by=created_by,
    )

    if extraction.primary_goal is not None:
        goal = await find_or_create_goal(pool, canonical_name=extraction.primary_goal.text, **goal_kwargs)
        await _link("goal", goal["id"], extraction.primary_goal.event_indices,
                    extraction.primary_goal.epistemic_status, extraction.primary_goal.confidence)
        counts["goals"] += 1

    for sg in extraction.subgoals:
        goal = await find_or_create_goal(pool, canonical_name=sg.text, **goal_kwargs)
        await _link("goal", goal["id"], sg.event_indices, sg.epistemic_status, sg.confidence)
        counts["goals"] += 1

    for claim_el in extraction.claims:
        # Single-trajectory extraction can never earn anything stronger
        # than these two tiers -- multi_trace_support/benchmark_support/
        # external_source_support are reserved for future cross-trace
        # corroboration this task does not build (Sec 18: preserve the
        # field, don't fabricate the evidence).
        generalization_level = (
            "single_trace_observation" if claim_el.epistemic_status == "observed"
            else "single_trace_inference"
        )
        claim_id = await capture_claim(
            pool,
            statement=claim_el.text,
            task_ids=[],
            justification_episode_id=episode_id,
            claim_type="trajectory_semantic",
            epistemic_status="observed" if claim_el.epistemic_status == "observed" else "inferred",
            extraction_version=f"{EXTRACTOR_ID}:{model}",
            confidence=claim_el.confidence,
            properties={"generalization_level": generalization_level, "extracted_by": EXTRACTOR_ID},
            owner_id=resolved_owner_id,
            visibility=resolved_visibility,
            scope_type=resolved_scope_type,
            scope_entity_id=resolved_scope_entity_id,
            ingestion_context_id=ingestion_context_id,
        )
        if claim_id:
            await _link("claim", claim_id, claim_el.event_indices, claim_el.epistemic_status, claim_el.confidence)
            counts["claims"] += 1

    for impl_el in extraction.implementations:
        identity = identify_implementation(impl_el.tool_name) or (impl_el.tool_name, "unknown", "tool")
        name, provider, kind = identity
        impl_row = await find_or_create_implementation_identity(
            pool, name=name, provider=provider, kind=kind, created_by=created_by,
            description=impl_el.role, owner_id=resolved_owner_id, visibility=resolved_visibility,
            scope_type=resolved_scope_type, scope_entity_id=resolved_scope_entity_id,
        )
        await _link("implementation", impl_row["id"], impl_el.event_indices, "observed", None)
        counts["implementations"] += 1

    for proc in extraction.candidate_procedures:
        steps: list[dict[str, Any]] = []
        for step in proc.steps:
            step_goal_id = None
            if step.subgoal_text:
                step_goal = await find_or_create_goal(pool, canonical_name=step.subgoal_text, **goal_kwargs)
                step_goal_id = step_goal["id"]
                counts["goals"] += 1
                # A step-level Goal is a real, distinct canonical object
                # this extraction created -- it earns its own citation
                # row exactly like the primary Goal/subgoals do, not just
                # an embedded goal_id inside the procedure's own steps
                # JSONB. Falls back to the procedure's own event_indices
                # if the step didn't cite any of its own.
                await _link(
                    "goal", step_goal_id,
                    step.event_indices or proc.event_indices,
                    proc.epistemic_status, proc.confidence,
                )
            steps.append({
                "description": step.description,
                "goal_id": step_goal_id,
                "event_refs": [event_id_by_index[i] for i in step.event_indices if i in event_id_by_index],
            })
        procedure_row = await capture_procedure(
            pool,
            name=proc.capability_statement[:120],
            goal=proc.capability_statement,
            steps=steps,
            source_episode_ids=[episode_id],
            provenance="system_pending_review",
            created_by=created_by,
            owner_id=resolved_owner_id,
            visibility=resolved_visibility,
            scope_type=resolved_scope_type,
            scope_entity_id=resolved_scope_entity_id,
        )
        if ingestion_context_id:
            await pool.execute(
                "UPDATE procedures SET ingestion_context_id = $1::uuid WHERE id = $2::uuid",
                ingestion_context_id, procedure_row["id"],
            )
        await _link("procedure", procedure_row["procedure_id"], proc.event_indices,
                    proc.epistemic_status, proc.confidence)
        counts["procedures"] += 1

    output_hash = _output_hash(raw_text)
    all_confidences = [
        el.confidence for el in (
            ([extraction.primary_goal] if extraction.primary_goal else [])
            + extraction.subgoals + extraction.claims + extraction.preconditions
            + extraction.failure_modes + extraction.recovery_patterns
            + extraction.verification_actions + extraction.reusable_elements
        )
    ] + [p.confidence for p in extraction.candidate_procedures]
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else None
    confidence_summary = {
        **counts,
        "uncertainties": len(extraction.uncertainties),
        "avg_confidence": avg_confidence,
        "min_confidence": min(all_confidences) if all_confidences else None,
    }
    await pool.execute(
        "UPDATE trajectory_extractions SET status='completed', output_hash=$2, "
        "confidence_summary=$3::jsonb, completed_at=now() WHERE id=$1::uuid",
        extraction_id, output_hash, json.dumps(confidence_summary),
    )

    return {
        "extraction_id": extraction_id,
        "episode_id": episode_id,
        "outcome": extraction.outcome,
        **counts,
        "uncertainties": extraction.uncertainties,
    }
