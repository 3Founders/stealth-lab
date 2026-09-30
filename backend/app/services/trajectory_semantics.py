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

import time
import hashlib
import json
import logging
from typing import Any, Literal, Optional

import asyncpg
from pydantic import BaseModel, Field, ValidationError

from app.services.claims import capture_claim
from app.services.goals import GoalQualityRejected, find_or_create_goal
from app.services.llm_json import parse_json_object
from app.services.governance import BudgetExceeded
from app.services.observations import _decode_json_field
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.procedures import capture_procedure
from app.utils.aio import run_blocking

log = logging.getLogger(__name__)

EXTRACTOR_ID = "trajectory_semantic_v1"
# v2, not v1: the prompt now carries the Pydantic contract verbatim (it used
# to describe names/enums the strict parser rejects -- measured 13/13 step-0
# calls rejected, 0 knowledge items, spend incurred). A prompt is a versioned
# artifact, so the fix gets its own version rather than silently reusing v1.
PROMPT_VERSION = "v3"   # v3 (2026-09-29): step role + check; claims name their subject and keep conditions
SCHEMA_VERSION = "v1"
DEFAULT_MODEL = "gemma-4-31B-it"

#: `llm_spend.operation` for this pass. Same string the guard and the
#: completion record use, so a pre-spend stop and its ledger row are
#: attributable to the same thing (hard rule 6: the gate and the writer
#: land together).
SEMANTICS_OP = "trajectory_semantics"

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
    # The concrete tool/mechanism the step used (e.g. "pytest"); stored as the step's `binding`.
    tool_name: Optional[str] = None
    event_indices: list[int] = Field(default_factory=list)
    # What kind of step this is (plan = locate/understand/reproduce, edit = change code, verify = run a check), so
    # per-step model routing and .stealth/run.md have something to key on; and how to tell the step worked.
    role: Optional[Literal["plan", "edit", "verify", "other"]] = None
    check: Optional[str] = None


class CandidateProcedure(BaseModel):
    model_config = {"extra": "forbid"}

    capability_statement: str = Field(min_length=1)
    steps: list[CandidateProcedureStep] = Field(min_length=1)
    event_indices: list[int] = Field(min_length=1)
    epistemic_status: EpistemicStatus = "inferred"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


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
    claims: list[SemanticElement] = Field(default_factory=list)
    preconditions: list[SemanticElement] = Field(default_factory=list)
    failure_modes: list[SemanticElement] = Field(default_factory=list)
    recovery_patterns: list[SemanticElement] = Field(default_factory=list)
    verification_actions: list[SemanticElement] = Field(default_factory=list)
    outcome: Optional[Outcome] = None
    reusable_elements: list[SemanticElement] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- prompt

def _schema_contract() -> str:
    """`TrajectorySemanticExtraction`'s own JSON Schema, rendered for the model.

    The prompt used to *describe* the contract in prose ("output STRICT JSON
    matching the given schema" -- with no schema given) and drifted from it in
    three independent, measured ways on the step-0 pilot: it said `goal` where
    the model wants `primary_goal`, `subgoal_text` where a step wants
    `description`, and `OBSERVED`/`INFERRED`/`GENERALIZED` where the enum is
    lowercase. A model that obeyed the prompt therefore produced a response the
    strict parser *must* reject: 13 of 13 calls failed and 0 knowledge items
    were produced while the run still spent money.

    Rendering the Pydantic model itself is the fix that cannot rot -- a field
    rename changes the prompt and the parser in the same edit. `title` keys are
    dropped (pure noise to a model, and a thing it might echo back, which
    `extra="forbid"` would then reject); `description` is kept because it is
    where the event_indices/UUID rationale lives.
    """
    def _strip_titles(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [_strip_titles(v) for v in node]
        return node

    return json.dumps(
        _strip_titles(TrajectorySemanticExtraction.model_json_schema()), separators=(",", ":")
    )


_SCHEMA_CONTRACT = _schema_contract()

_SYSTEM_PROMPT = """You perform semantic extraction over one agent trajectory (a bounded, \
numbered sequence of tool-call events).

Output ONE JSON object that validates against the JSON Schema below, and nothing \
else -- no prose, no explanation, no schema keywords (`$schema`, `title`, \
`additionalProperties`, `$defs`) in your answer. The schema is the contract: use \
its field names exactly, its enum values exactly (they are lowercase), and no \
field that is not in it.

<schema>
""" + _SCHEMA_CONTRACT + """
</schema>

Rules:
- Every extracted element MUST cite `event_indices`: the 1-based numbers of the events (from the \
numbered list given to you) that directly support it. Never cite an index outside the given range. \
Never leave event_indices empty for anything except `uncertainties` (which is plain text, not an \
element).
- Write `primary_goal`, every `subgoals` entry and every candidate procedure's `capability_statement` as a GENERALIZABLE outcome another agent could reuse on a different codebase ("Add a --version option to a CLI tool"), never as an edit to this one: no file paths, no module or function names, no code or command syntax, no backticks. Repository specifics belong in `claims`, which cite the events.
- Tag every element's `epistemic_status` with the exact lowercase enum value the schema allows:
  "observed" = directly present in the trajectory (e.g. "a file was read then edited").
  "inferred" = a reasonable semantic interpretation of what happened (e.g. "the agent was debugging \
a test failure").
  "generalized" = a candidate reusable insight that plausibly extends beyond this one run (e.g. "for \
generated files, editing the source and regenerating beats editing the generated file directly").
  Default to "inferred" when unsure; never claim "observed" for something not literally present.
- A `candidate_procedures[]` entry requires an ACTUAL ordered sequence of steps genuinely present in \
the trajectory -- never invent one from a single ambiguous action. If the trajectory has no coherent \
reusable procedure, leave candidate_procedures empty; do not force one.
- Each procedure step's `description` states what the step did, and its `subgoal_text` names the \
STEP'S GOAL (e.g. "locate the failing test"), not the literal tool call (e.g. NOT "run pytest \
tests/test_foo.py") -- concrete tools belong in `tool_name`, not in the procedure step text. \
`tool_name` names the CONCRETE tool/mechanism the step actually used (e.g. "pytest", "ripgrep"); \
never invent a tool that wasn't actually called.
- Each step's `role` is "plan" (locate, understand, reproduce), "edit" (change code), "verify" (run a check) or \
"other". Its `check` is the concrete way the trajectory showed the step worked -- the command and the result that \
proved it (e.g. "pytest tests/test_x.py::test_y passes") -- or omitted when the trajectory shows none. Never invent a \
check the trajectory did not run.
- `primary_goal` is the trajectory's single main objective; `subgoals` are the intermediate objectives.
- `claims[]` are semantic propositions ("the test failure disappeared after regenerating the client \
from the schema"), never a restatement of raw telemetry ("a file was modified"). Write every claim, failure mode, \
recovery pattern, precondition and verification action as a plain sentence that (a) names exactly what it is about \
(the function, option, library, command or behaviour -- never "it" or "this"), (b) keeps the conditions under which \
it holds (versions, platform, configuration, "in this repository"), and (c) is grounded in what the events show. \
For a comparison, state both sides, what was compared and which direction is better. Drop anything unbound, \
hypothetical or implausible (e.g. an improvement over 100%, a negative duration).
- Extract real value from a FAILED trajectory too: attempted goal, failure point, failed \
precondition, recovery attempts, and any repeated-action/thrashing pattern belong in \
failure_modes/recovery_patterns -- do not leave everything empty just because the run failed.
- Any list may legitimately be empty -- that is a real answer, not a failure. Do not fabricate an \
element to make a list non-empty.
- If you are unsure how to classify something, put a short note in `uncertainties` instead of \
forcing it into a typed field with a low-quality guess.
"""


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > _MAX_FIELD_CHARS:
        return text[:_MAX_FIELD_CHARS] + "...[truncated]"
    return text


def _event_line(i: int, event: dict) -> str:
    kind = event.get("canonical_event_type") or event.get("event_type") or "?"
    tool = event.get("tool_name") or ""
    tool_input = _truncate(event.get("tool_input") or {})
    tool_output = _truncate(event.get("tool_output") or {})
    success = event.get("success")
    success_str = "" if success is None else (" ok" if success else " FAILED")
    return f"{i}. [{kind}]{success_str} {tool} input={tool_input} output={tool_output}"


def _numbered_event_lines(events: list[dict]) -> list[str]:
    return [_event_line(i, event) for i, event in enumerate(events, start=1)]


def _build_user_prompt(goal_text: Optional[str], events: list[dict], lines: Optional[list[str]] = None) -> str:
    """`lines` (from trace_compaction) replaces the default one-line-per-event
    rendering when the trajectory was semantically compacted."""
    header = f"Declared/prior goal text (may be absent or wrong): {goal_text or '(none)'}\n"
    body_lines = lines if lines is not None else _numbered_event_lines(events)
    return f"{header}Events (1-{len(body_lines)}):\n" + "\n".join(body_lines)


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


_ENUM_KEYS = ("epistemic_status", "outcome")


def _lowercase_enums(node: Any) -> Any:
    """"OBSERVED" -> "observed", "Success" -> "success": case is never the meaning, and the schema's Literals
    are lowercase. Anything else stays exactly as the model wrote it."""
    if isinstance(node, dict):
        return {k: (v.strip().lower() if k in _ENUM_KEYS and isinstance(v, str) else _lowercase_enums(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_lowercase_enums(v) for v in node]
    return node


def parse_extraction_response(text: str, *, max_index: int) -> TrajectorySemanticExtraction:
    """Strict parse: valid JSON, valid against the Pydantic schema. Raises
    ExtractionTransientFailure on anything else -- same "never silently
    substitute a degraded result" discipline GroundedHybridExtractor
    uses. Out-of-range event_indices are filtered (not fatal) per element;
    an element left with zero valid indices after filtering is dropped
    and recorded in `uncertainties` instead of persisted as canonical
    knowledge with fabricated evidence."""
    # BLOCKERS I3: models fence their JSON and capitalise the status words; neither is a real schema miss.
    payload = parse_json_object(text or "")
    if payload is None:
        raise ExtractionTransientFailure(
            f"semantic extraction response was not valid JSON: {(text or '')[:200]!r}"
        )
    payload = _lowercase_enums(payload)

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
    compaction_judge: Any = None,
    write_procedures: bool = True,
    task_goal: Optional[dict] = None,
    embedder: Any = None,
) -> dict:
    """
    `task_goal` ({"id", "canonical_name"}, 2026-09-29): the caller already knows which Goal this run attempted (a
    benchmark task, shared by every source of that task -- migration 129). It then IS the run's primary Goal (the
    extracted primary goal is cited against it instead of creating a second Goal), every Procedure achieves it
    (and goes through Procedure identity, so the same way from another run or source is reused, not duplicated),
    and every Claim carries it as its Goal. Without it, behaviour is unchanged.

    Also stored since 2026-09-29: the extracted preconditions (on each Procedure), verification actions (Claims
    linked to each Procedure as VERIFICATION), and failure modes linked to each Procedure as FAILURE_MODE -- all were
    extracted and paid for before, and then discarded.
    `write_procedures=False` (2026-09-29): the caller knows the run FAILED (e.g. a benchmark-graded trajectory with
    resolved=0). Goals and Claims -- including the failure modes and recovery patterns -- are still written, but no
    Procedure is: a way that did not work is evidence of what fails, never a way to follow
    (docs/ingestion_sources_plan.md, hard rule 4). The withheld candidates are counted in the result.

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
    silently degrades. One exception to that rule, on purpose: a
    `BudgetExceeded` from the pre-spend `ingest_budget.guard` propagates
    as itself rather than as a transient failure, because it is a cost
    stop and a retry loop must not turn it into a spend.

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
    # Raw events -> (size-gated) semantic compaction -> prompt lines. Small
    # trajectories pass through exactly as before (first _MAX_EVENTS_IN_PROMPT
    # events, one line each). Every prompt index keeps a citation back to the
    # real trace_event id(s) it came from (prepared.index_refs).
    decoded_events = [
        {
            **e,
            "tool_input": _decode_json_field(e.get("tool_input")),
            "tool_output": _decode_json_field(e.get("tool_output")),
        }
        for e in events
    ]
    from app.services.trace_compaction import prepare_events_for_extraction

    prepared = await prepare_events_for_extraction(
        pool, episode, _resolve_goal_text(episode), decoded_events,
        line_fn=_event_line, max_events=_MAX_EVENTS_IN_PROMPT, judge=compaction_judge,
    )

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
    prompt = _build_user_prompt(goal_text, decoded_events, lines=prepared.lines)
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
        # Hard rule: never let a blocking call run inside an `async def`. This
        # module is called from the FastAPI API, the MCP server (one process,
        # one event loop) and headless workers, so a synchronous
        # `chat.completions.create` invoked inline froze every other coroutine
        # for the length of the call.
        #
        # `run_blocking` rather than a bare `await` because the injected
        # `client` is a sync `OpenAI` (`ingestion_jobs._general_compute_client()`
        # / `_extraction_client()` build one, and `_RotatingOpenAIClient` only
        # exposes a sync `.create`). `run_blocking` puts a sync call in the
        # default thread pool and awaits a coroutine if the callable returns
        # one, so an `AsyncOpenAI` injected by a future caller also works -- the
        # previous shape had neither: a sync client blocked the loop, and an
        # async one silently produced an un-awaited coroutine that died on
        # `.choices` (`'coroutine' object has no attribute 'choices'`) after the
        # call had already been paid for.
        #
        # The pre-spend guard is a no-op unless an ingestion worker installed a
        # budget, and it is checked BEFORE the call, so the daily cap is a real
        # ceiling rather than a post-hoc tally.
        from app.services import ingest_budget

        await ingest_budget.guard(SEMANTICS_OP)
        _llm_t0 = time.monotonic()
        response = await run_blocking(
            client.chat.completions.create,
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4000,
        )
        await ingest_budget.record_completion(
            model, SEMANTICS_OP, getattr(response, "usage", None)
        )
        raw_text = response.choices[0].message.content.strip()
        llm_seconds = round(time.monotonic() - _llm_t0, 1)
    except BudgetExceeded:
        # A cost stop, not a provider failure. It is deliberately NOT wrapped in
        # ExtractionTransientFailure: a caller that retries transient failures
        # must not burn attempts against a budget that is already spent, and the
        # ingestion worker treats a raised BudgetExceeded as "hand the job
        # back, do not spend an attempt".
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid",
            extraction_id, f"budget guard refused the call: op={SEMANTICS_OP}",
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- any transport/client failure is real
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid",
            extraction_id, repr(exc),
        )
        raise ExtractionTransientFailure(f"semantic extraction LLM call failed: {exc!r}") from exc

    try:
        extraction = parse_extraction_response(raw_text, max_index=len(prepared.lines))
    except ExtractionTransientFailure as exc:
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid",
            extraction_id, str(exc),
        )
        raise

    async def _persist() -> dict[str, Any]:
        """Write the extracted objects and close the row.

        Split out of the enclosing function for ONE reason: so the handler
        below can cover every persistence step, not just the LLM call and the
        parse. A rejection raised while writing the objects (observed on the
        step-0 re-run: `GoalQualityRejected` from `find_or_create_goal`, twice
        in ten passes) otherwise leaves the row at 'pending' -- which reads as
        "never ran" to this module's own re-extraction query and leaves a
        report that says `completed 8, failed 0` beside a counter that says
        two failed.
        """
        # index -> [trace_event ids]; a compacted line can stand for several events.
        event_ids_by_index = prepared.index_refs
        counts = {"goals": 0, "claims": 0, "procedures": 0, "failure_claims": 0, "procedures_withheld": 0}
        written: dict[str, Any] = {"primary_goal_id": None, "goal_ids": [], "claim_ids": [], "procedure_rows": []}

        async def _link(object_type: str, object_id: str, indices: list[int],
                         epistemic_status: str, confidence: Optional[float] = None) -> None:
            event_refs = list(dict.fromkeys(r for i in indices for r in event_ids_by_index.get(i, [])))
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
            # With an embedder, a new Goal gets its vector at creation, so identity resolution also finds
            # candidates by meaning (not only by keywords). Ingestion passes one (2026-09-30).
            **({"embedder": embedder} if embedder is not None else {}),
        )

        async def _goal_or_none(text: str) -> Optional[dict]:
            """The goal-quality gate is right to refuse a goal that names a literal file path or code -- but one such
            subgoal must not throw away the whole extraction after the model was already paid (measured 2026-09-29:
            4 of 13 extractions lost to it). Drop that goal, keep everything else, and say so in `uncertainties`."""
            try:
                return await find_or_create_goal(pool, canonical_name=text, **goal_kwargs)
            except GoalQualityRejected as exc:
                dropped_goals.append(f"goal dropped by the quality gate: {str(exc)[:200]}")
                return None

        dropped_goals: list[str] = []
        if task_goal is not None:
            # The caller knows the task's Goal: it IS the primary Goal. The extracted primary goal only cites events.
            written["primary_goal_id"] = str(task_goal["id"])
            written["goal_ids"].append(str(task_goal["id"]))
            if extraction.primary_goal is not None:
                await _link("goal", str(task_goal["id"]), extraction.primary_goal.event_indices,
                            extraction.primary_goal.epistemic_status, extraction.primary_goal.confidence)
        elif extraction.primary_goal is not None:
            goal = await _goal_or_none(extraction.primary_goal.text)
            if goal is not None:
                await _link("goal", goal["id"], extraction.primary_goal.event_indices,
                            extraction.primary_goal.epistemic_status, extraction.primary_goal.confidence)
                counts["goals"] += 1
                written["primary_goal_id"] = str(goal["id"])
                written["goal_ids"].append(str(goal["id"]))

        for sg in extraction.subgoals:
            goal = await _goal_or_none(sg.text)
            if goal is not None:
                await _link("goal", goal["id"], sg.event_indices, sg.epistemic_status, sg.confidence)
                counts["goals"] += 1
                written["goal_ids"].append(str(goal["id"]))

        claim_goal = {"goal_id": written["primary_goal_id"]} if written["primary_goal_id"] else {}
        linked: dict[str, list[str]] = {"FAILURE_MODE": [], "VERIFICATION": []}

        async def _claim(el: SemanticElement, claim_type: str, extra: dict) -> Optional[str]:
            # Single-trajectory extraction can never earn anything stronger than these two tiers --
            # multi_trace_support/benchmark_support/external_source_support are reserved for future cross-trace
            # corroboration this task does not build (Sec 18: preserve the field, don't fabricate the evidence).
            claim_id = await capture_claim(
                pool,
                statement=el.text,
                task_ids=[],
                justification_episode_id=episode_id,
                claim_type=claim_type,
                epistemic_status="observed" if el.epistemic_status == "observed" else "inferred",
                extraction_version=f"{EXTRACTOR_ID}:{model}",
                confidence=el.confidence,
                properties={"generalization_level": "single_trace_observation"
                            if el.epistemic_status == "observed" else "single_trace_inference",
                            "extracted_by": EXTRACTOR_ID, **claim_goal, **extra},
                owner_id=resolved_owner_id,
                visibility=resolved_visibility,
                scope_type=resolved_scope_type,
                scope_entity_id=resolved_scope_entity_id,
                ingestion_context_id=ingestion_context_id,
            )
            if claim_id:
                await _link("claim", claim_id, el.event_indices, el.epistemic_status, el.confidence)
                counts["claims"] += 1
                written["claim_ids"].append(str(claim_id))
            return str(claim_id) if claim_id else None

        for claim_el in extraction.claims:
            await _claim(claim_el, "trajectory_semantic", {})

        # Failure modes, recovery patterns and verification actions were extracted (and paid for) but never stored
        # before 2026-09-29, so a failed run left no record of WHY it failed and a Procedure no record of its checks.
        for claim_type, elements in (("failure_mode", extraction.failure_modes),
                                     ("recovery_pattern", extraction.recovery_patterns)):
            for el in elements:
                cid = await _claim(el, claim_type, {"run_outcome": extraction.outcome})
                if cid:
                    counts["failure_claims"] += 1
                    if claim_type == "failure_mode":
                        linked["FAILURE_MODE"].append(cid)
        for el in extraction.verification_actions:
            cid = await _claim(el, "verification", {"run_outcome": extraction.outcome})
            if cid:
                linked["VERIFICATION"].append(cid)

        preconditions = [{"source": "trajectory_extraction", "description": el.text}
                         for el in extraction.preconditions]
        if not write_procedures:
            counts["procedures_withheld"] = len(extraction.candidate_procedures)
        for proc in (extraction.candidate_procedures if write_procedures else []):
            steps: list[dict[str, Any]] = []
            for order, step in enumerate(proc.steps):
                step_goal_id = None
                if step.subgoal_text:
                    step_goal = await _goal_or_none(step.subgoal_text)
                    if step_goal is not None:
                        step_goal_id = step_goal["id"]
                        counts["goals"] += 1
                        # A step-level Goal is a real, distinct canonical object this extraction created -- it earns
                        # its own citation row like the primary Goal/subgoals do. (Not retrievable on its own until
                        # it has a Procedure: goal_search_index.has_procedures, migration 130.)
                        await _link(
                            "goal", step_goal_id,
                            step.event_indices or proc.event_indices,
                            proc.epistemic_status, proc.confidence,
                        )
                step_doc: dict[str, Any] = {
                    "order": order,
                    "description": step.description,
                    "do": step.description,
                    "goal_id": step_goal_id,
                    "event_refs": list(dict.fromkeys(r for i in step.event_indices for r in event_ids_by_index.get(i, []))),
                    "source_locator": {"source_id": f"trajectory-extraction:{extraction_id}", "granularity": "span"},
                }
                if step.role:
                    step_doc["role"] = step.role
                if step.check:
                    step_doc["check"] = step.check.strip()
                if step.tool_name:
                    step_doc["binding"] = {"kind": "tool", "tool": step.tool_name.strip()}
                steps.append(step_doc)
            capture_kwargs: dict[str, Any] = {}
            if task_goal is not None:
                # The same way from another run or source of this task is reused (judged identity), not duplicated.
                capture_kwargs = {"procedure_dedup": True,
                                  "source_key": f"trajectory:{task_goal['id']}:{proc.capability_statement[:80]}"}
            procedure_row = await capture_procedure(
                pool,
                name=proc.capability_statement[:120],
                goal=str(task_goal["canonical_name"]) if task_goal is not None else proc.capability_statement,
                steps=steps,
                preconditions=preconditions,
                failure_conditions=[{"source": "trajectory_extraction", "description": el.text}
                                    for el in extraction.failure_modes],
                source_locator={"source_id": f"trajectory-extraction:{extraction_id}", "granularity": "document"},
                source_episode_ids=[episode_id],
                provenance="system_pending_review",
                created_by=created_by,
                owner_id=resolved_owner_id,
                visibility=resolved_visibility,
                scope_type=resolved_scope_type,
                scope_entity_id=resolved_scope_entity_id,
                **({"goal_embedder": embedder} if embedder is not None else {}),
                **capture_kwargs,
            )
            if ingestion_context_id and not procedure_row.get("reused"):
                await pool.execute(
                    "UPDATE procedures SET ingestion_context_id = $1::uuid WHERE id = $2::uuid",
                    ingestion_context_id, procedure_row["id"],
                )
            await _link("procedure", procedure_row["procedure_id"], proc.event_indices,
                        proc.epistemic_status, proc.confidence)
            counts["procedures"] += 1
            written["procedure_rows"].append({"id": str(procedure_row["id"]),
                                              "procedure_id": str(procedure_row["procedure_id"]),
                                              "name": proc.capability_statement[:120],
                                              "reused": bool(procedure_row.get("reused"))})
            # K4: this run's failure modes and verification actions are attached to the way itself, so
            # procedures.md and the claims resource show "this fails because ..." and "check it by ..." with it.
            if linked["FAILURE_MODE"] or linked["VERIFICATION"]:
                from app.services.procedure_claim_refs import add_procedure_claim_ref

                version = int(await pool.fetchval("SELECT version FROM procedures WHERE id = $1::uuid",
                                                  procedure_row["id"]) or 1)
                for role, claim_ids in linked.items():
                    for cid in claim_ids:
                        await add_procedure_claim_ref(
                            pool, procedure_id=str(procedure_row["procedure_id"]), procedure_version=version,
                            claim_id=cid, role=role, ref_origin="derived",
                            extractor_version=f"{EXTRACTOR_ID}:{PROMPT_VERSION}",
                            ingestion_context_id=ingestion_context_id, created_by=created_by)

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
            # The dict, NOT json.dumps(confidence_summary). The pool registers a
            # jsonb codec (`app/db/session.py::_init_connection`) that already
            # encodes it, so pre-encoding double-encodes and the column ends up
            # holding a JSON *string* -- measured on the step-0 re-run:
            # `jsonb_typeof(confidence_summary)` = "string", so
            # `confidence_summary->>'goals'` is NULL for every completed
            # extraction and this module's per-extraction yield counters are
            # unreadable. Same trap already documented in
            # `route_decision.py:450` and `trace_worker.py:1280`.
            extraction_id, output_hash, confidence_summary,
        )

        return {
            "extraction_id": extraction_id,
            "episode_id": episode_id,
            "outcome": extraction.outcome,
            **counts,
            **written,
            "uncertainties": [*extraction.uncertainties, *dropped_goals],
            "compaction": prepared.compaction,
        }

    try:
        _persist_t0 = time.monotonic()
        out = await _persist()
        # Where an extraction spends its time (throughput diagnosis, 2026-09-30).
        out["timings_s"] = {"llm": llm_seconds, "persist": round(time.monotonic() - _persist_t0, 1)}
        return out
    except Exception as exc:  # noqa: BLE001 -- any persistence failure is real
        await pool.execute(
            "UPDATE trajectory_extractions SET status='failed', error=$2, completed_at=now() "
            "WHERE id=$1::uuid AND status='pending'",
            extraction_id, f"persistence failed after a successful parse: {exc!r}",
        )
        raise
