"""
Extraction mechanisms (procedure extraction, memory-substrate map).
ExtractionStrategy is the MECHANISM layer -- shaped like
app.execution.htn_agent.SchedulerStrategy, this repo's existing pattern
for pluggable behavior, not a new convention. Changing a mechanism means
a new subclass and a deploy; changing a VARIANT of a mechanism (a
prompt, a model, a scope) is registry.py's job, not this file's -- see
that module's docstring for the full split.

ONLY TWO FIELDS ACTUALLY NEED A MODEL: capability_statement and step
phrasing. Sorting ExtractedProcedure's fields by whether they require
generalization (see derive.py's own docstring for the full table) shows
everything else -- preconditions, scope, the step skeleton, slots,
failure_conditions -- is a real derivation against real state. That is
why GroundedHybridExtractor below makes exactly one small, bounded LLM
call over a COMPRESSED summary, never the raw episode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import asyncpg
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_preconditions,
    derive_scope,
    derive_slots,
    derive_step_skeleton,
    literal_steps_from_skeleton,
)
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import (
    ExtractedProcedure,
    ExtractionTransientFailure,
    ProcedureStep,
)


class ExtractionStrategy(ABC):
    @abstractmethod
    async def extract(
        self, pool: asyncpg.Pool, evidence: ProcedureEvidence, *,
        repo_root: Optional[str] = None, entry_seed_files: Optional[list[str]] = None,
    ) -> Optional[ExtractedProcedure]:
        """None means a genuine abstain (no real procedure in this
        episode). A strategy that cannot produce a trustworthy result for
        an infrastructure reason (LLM call failed, response didn't parse)
        must raise ExtractionTransientFailure instead of returning
        anything -- never silently substitute a different strategy's
        output."""
        ...


class DeterministicExtractor(ExtractionStrategy):
    """
    The honest baseline -- no LLM, no ambiguity, produces a literal,
    non-generalized procedure. Kept per ticket 04's own rule ("no study
    reports a rule-based baseline before adding a model... build both
    and measure the delta rather than assume one"), and because it is
    the real, always-available fallback when no LLM client is configured
    or the model call fails -- migration 20 seeds it enabled so this
    fallback always has somewhere to land.
    """

    async def extract(
        self, pool: asyncpg.Pool, evidence: ProcedureEvidence, *,
        repo_root: Optional[str] = None, entry_seed_files: Optional[list[str]] = None,
    ) -> ExtractedProcedure:
        skeleton = derive_step_skeleton(evidence)
        steps = literal_steps_from_skeleton(skeleton)
        preconditions = await derive_preconditions(pool, evidence)
        scope = await derive_scope(pool, evidence)
        slots = derive_slots(evidence, repo_root=repo_root, entry_seed_files=entry_seed_files or [])
        failure_conditions = derive_failure_conditions(evidence)

        return ExtractedProcedure(
            name=evidence.goal_text[:100],
            goal=evidence.goal_text,
            # Literal, deliberately -- a DeterministicExtractor output
            # reads as exactly what it is (a replay skeleton, not a
            # generalized method), not disguised as an abstraction it
            # cannot actually produce without a model.
            capability_statement=evidence.goal_text[:200],
            steps=steps, slots=slots, preconditions=preconditions,
            scope=scope, failure_conditions=failure_conditions,
        )


_ABSTRACTION_SYSTEM_PROMPT = """You compress a coding episode's tool-call pattern into a reusable \
procedure description. You are given: the concrete goal that was accomplished, and a compressed \
sequence of tool-call groups (e.g. "Read x3, Edit x1, Bash x2").

Reply with ONLY a JSON object, no other text, matching exactly this shape:
{"capability_statement": "<one abstract sentence describing the general skill this demonstrates, \
with NO file names, symbol names, repo names, or other specifics from this episode -- it must \
describe something that would apply to a DIFFERENT project doing a similar kind of work>",
 "step_phrases": ["<generalized step phrase>", ...]}

`step_phrases` must have EXACTLY one entry per tool-call group given, in the SAME order, each \
describing the ACTION pattern (e.g. "locate the relevant files", "apply a targeted edit", \
"run the test suite"), never naming a specific file or symbol.

If you cannot produce a genuinely abstract capability statement, reply with exactly this JSON
object instead: {"abstain": true}
"""


class GroundedHybridExtractor(ExtractionStrategy):
    """
    The default extractor. Everything except capability_statement and
    step phrasing is derived (see derive.py) -- this class's OWN job is
    narrow: one small LLM call over a compressed summary, asked for
    exactly those two things, merged onto the derived skeleton.

    THE COMPOUNDING BENEFIT this design exists for: preconditions
    derived from project_state() are, by construction, already in
    environment_probe.PROBE_PREDICATE_VOCABULARY, so they cannot fail
    V1 (groundedness) -- a pure-LLM extractor would need a validator to
    catch an invented precondition; this class cannot produce one in
    the first place, because it never asks the model for one.

    No silent degradation: an LLM call that fails, a response that
    doesn't parse, or a misconfigured client (no `self._client` -- should
    never happen in practice, since `_select_strategy()` only ever
    constructs this class with a real client, but treated as a real
    failure rather than silently patched over if it somehow does) all
    raise ExtractionTransientFailure. The caller (extract_procedure(),
    then the ingestion job handler) must let it propagate -- no procedure
    is written under this extractor's tag unless a real LLM abstraction
    actually succeeded. An explicit `{"abstain": true}` response is
    different: it is the model's genuine, final answer that this episode
    has no real procedure in it, so `extract()` returns None rather than
    raising.
    """

    def __init__(self, client: Any, model: str = "gemma-4-31B-it", temperature: float = 0.2):
        self._client = client
        self._model = model
        self._temperature = temperature

    async def extract(
        self, pool: asyncpg.Pool, evidence: ProcedureEvidence, *,
        repo_root: Optional[str] = None, entry_seed_files: Optional[list[str]] = None,
    ) -> Optional[ExtractedProcedure]:
        skeleton = derive_step_skeleton(evidence)
        preconditions = await derive_preconditions(pool, evidence)
        scope = await derive_scope(pool, evidence)
        slots = derive_slots(evidence, repo_root=repo_root, entry_seed_files=entry_seed_files or [])
        failure_conditions = derive_failure_conditions(evidence)

        if self._client is None:
            raise ExtractionTransientFailure(
                "GroundedHybridExtractor was selected with no LLM client configured",
            )
        if not skeleton:
            # Nothing to summarize -- a structural fact about this
            # episode's evidence, not something a retry will change. This
            # is a genuine abstain, not a failure.
            return None

        summary = "; ".join(f"{g.tool_name}" + (f" x{g.count}" if g.count > 1 else "")
                             for g in skeleton)
        user_prompt = f"Goal: {evidence.goal_text}\nTool-call pattern: {summary}"

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _ABSTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                max_tokens=300,
            )
            text = response.choices[0].message.content.strip()
        except Exception as exc:  # noqa: BLE001 -- any client/transport
            # failure (timeout, 429, auth, ...) is real and must be
            # surfaced, never silently patched over with a different
            # strategy's output.
            raise ExtractionTransientFailure(
                f"LLM call failed: {exc!r}", is_rate_limit=_looks_like_rate_limit(exc),
            ) from exc

        parsed = _parse_abstraction_response(text, expected_step_count=len(skeleton))
        if parsed is _ABSTAIN:
            return None
        if parsed is None:
            raise ExtractionTransientFailure(
                f"LLM response did not parse into the expected shape: {text[:200]!r}",
            )
        capability_statement, step_phrases = parsed

        # _parse_abstraction_response has already enforced
        # len(step_phrases) == len(skeleton), so this zip cannot silently
        # truncate -- a mismatch became a parse failure above.
        #
        # Both halves are kept, where before only the abstract phrase
        # survived: `goal` is the model's generalized phrasing (section
        # 13's own field), `action` is the literal mechanical description
        # derived from the real tool log, and allowed_implementations
        # names the tool structurally. Readers that want the abstract
        # form should prefer `goal` and fall back to `action`.
        steps = [
            ProcedureStep(
                order=i,
                action=f"Call {g.tool_name}" + (f" ({g.count}x)" if g.count > 1 else ""),
                goal=phrase,
                allowed_implementations=[{"type": "tool", "name": g.tool_name}],
            )
            for i, (g, phrase) in enumerate(zip(skeleton, step_phrases), start=1)
        ]

        return ExtractedProcedure(
            name=evidence.goal_text[:100],
            goal=evidence.goal_text,
            capability_statement=capability_statement,
            steps=steps, slots=slots, preconditions=preconditions,
            scope=scope, failure_conditions=failure_conditions,
        )


class _AbstractionResponse(BaseModel):
    """The real, enforced schema for GroundedHybridExtractor's one LLM
    call -- Pydantic validates shape (non-empty capability_statement,
    non-empty step_phrases, every phrase itself non-empty), not just a
    manual isinstance check. `model_validate` raising ValidationError is
    treated identically to malformed JSON: a parse failure, which the
    caller turns into an ExtractionTransientFailure."""

    capability_statement: str = Field(min_length=1)
    step_phrases: list[str] = Field(min_length=1)

    @field_validator("capability_statement")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("capability_statement must not be blank")
        return v

    @field_validator("step_phrases")
    @classmethod
    def _phrases_not_blank(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v]
        if not all(cleaned):
            raise ValueError("step_phrases must not contain a blank entry")
        return cleaned


_ABSTAIN = object()  # sentinel: distinguishes a genuine {"abstain": true}
                      # response from a parse failure (None) -- the caller
                      # treats the former as a real answer, the latter as
                      # an ExtractionTransientFailure.


def _looks_like_rate_limit(exc: Exception) -> bool:
    """Best-effort rate-limit detection across OpenAI-compatible clients,
    which don't share a common exception hierarchy -- checked by
    `status_code`/`code` attributes first (most client libraries set one
    of these on the real exception object), falling back to the message
    text. False negatives just mean a rate-limited job requeues
    immediately instead of after a backoff -- not silently lost, so this
    only needs to be good enough, not perfect."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or status == "429":
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _parse_abstraction_response(
    text: str, *, expected_step_count: int,
) -> Optional[tuple[str, list[str]]]:  # or the `_ABSTAIN` sentinel; see docstring
    """
    Pure, testable without a client at all. Three distinguishable
    outcomes: a valid `(capability_statement, step_phrases)` tuple; the
    `_ABSTAIN` sentinel for an explicit `{"abstain": true}` response (a
    real, final answer); or `None` for anything else that doesn't parse
    cleanly -- malformed JSON, a schema-invalid payload (Pydantic
    `_AbstractionResponse`), or a `step_phrases` list whose length
    doesn't match the derived skeleton's own step count (an LLM inventing
    or dropping steps relative to what actually happened). The caller
    raises ExtractionTransientFailure on `None`, never silently
    substitutes a different strategy's output.

    Real JSON parsing (matching claim_extraction.py's own
    prompted-JSON-plus-schema-validation convention). A model that wraps
    its JSON in a code fence or leading prose is still accepted (the
    fence/prose is stripped) since that is a real, observed response
    shape from some OpenAI-compatible providers, not a parse-success
    worth losing an otherwise-valid extraction over.
    """
    import json

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("abstain"):
        return _ABSTAIN  # type: ignore[return-value]

    try:
        validated = _AbstractionResponse.model_validate(parsed)
    except ValidationError:
        return None

    if len(validated.step_phrases) != expected_step_count:
        return None
    return validated.capability_statement, validated.step_phrases
