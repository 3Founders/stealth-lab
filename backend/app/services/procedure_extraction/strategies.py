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

from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_preconditions,
    derive_scope,
    derive_slots,
    derive_step_skeleton,
    literal_steps_from_skeleton,
)
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import ExtractedProcedure, ProcedureStep


class ExtractionStrategy(ABC):
    @abstractmethod
    async def extract(
        self, pool: asyncpg.Pool, evidence: ProcedureEvidence, *,
        repo_root: Optional[str] = None, entry_seed_files: Optional[list[str]] = None,
    ) -> ExtractedProcedure: ...


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

Produce exactly two things, each on its own line prefixed by its label:
CAPABILITY: <one abstract sentence describing the general skill this demonstrates, with NO file \
names, symbol names, repo names, or other specifics from this episode -- it must describe \
something that would apply to a DIFFERENT project doing a similar kind of work>
STEPS: <a semicolon-separated list of generalized step phrases matching the tool-call groups' \
ORDER and COUNT, each phrase describing the ACTION pattern (e.g. "locate the relevant files", \
"apply a targeted edit", "run the test suite"), never naming a specific file or symbol>

If you cannot produce a genuinely abstract capability statement, reply with exactly: ABSTAIN
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

    Degradation is explicit, not silent: no client, an API failure, a
    response that doesn't parse into the CAPABILITY/STEPS shape, or an
    explicit ABSTAIN all fall back to DeterministicExtractor's output --
    marked via `used_fallback` on the caller side (__init__.py), never
    silently presented as a successful abstraction.
    """

    def __init__(self, client: Any, model: str = "gemma-4-31B-it", temperature: float = 0.2):
        self._client = client
        self._model = model
        self._temperature = temperature
        self._fallback = DeterministicExtractor()

    async def extract(
        self, pool: asyncpg.Pool, evidence: ProcedureEvidence, *,
        repo_root: Optional[str] = None, entry_seed_files: Optional[list[str]] = None,
    ) -> ExtractedProcedure:
        skeleton = derive_step_skeleton(evidence)
        preconditions = await derive_preconditions(pool, evidence)
        scope = await derive_scope(pool, evidence)
        slots = derive_slots(evidence, repo_root=repo_root, entry_seed_files=entry_seed_files or [])
        failure_conditions = derive_failure_conditions(evidence)

        if self._client is None or not skeleton:
            return await self._fallback.extract(
                pool, evidence, repo_root=repo_root, entry_seed_files=entry_seed_files,
            )

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
        except Exception:  # noqa: BLE001 -- an LLM call's own real failure
            # must degrade to the honest fallback, never propagate and
            # abort extraction outright.
            return await self._fallback.extract(
                pool, evidence, repo_root=repo_root, entry_seed_files=entry_seed_files,
            )

        parsed = _parse_abstraction_response(text, expected_step_count=len(skeleton))
        if parsed is None:
            return await self._fallback.extract(
                pool, evidence, repo_root=repo_root, entry_seed_files=entry_seed_files,
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


def _parse_abstraction_response(
    text: str, *, expected_step_count: int,
) -> Optional[tuple[str, list[str]]]:
    """
    Pure, testable without a client at all -- strict parsing, not
    forgiving: a response missing either label, an explicit ABSTAIN, or
    a STEPS list whose length doesn't match the derived skeleton's own
    step count is treated as a parse failure (returns None), triggering
    the caller's fallback rather than silently accepting a malformed
    result. The step-count check specifically catches an LLM inventing
    or dropping steps relative to what actually happened.
    """
    if text.strip() == "ABSTAIN":
        return None
    capability = None
    steps: Optional[list[str]] = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("CAPABILITY:"):
            capability = line[len("CAPABILITY:"):].strip()
        elif line.startswith("STEPS:"):
            raw = line[len("STEPS:"):].strip()
            steps = [s.strip() for s in raw.split(";") if s.strip()]
    if not capability or not steps:
        return None
    if len(steps) != expected_step_count:
        return None
    return capability, steps
