"""
The extraction contract for skill/document ingestion (founder directive,
2026-09-15: "remove the deterministic parser... we want the LLM call to
generate the structured things"). Mirrors
app/services/procedure_extraction/schema.py's own discipline exactly --
strict Pydantic, not a plain dict: a malformed extraction must fail
loudly at the schema boundary, not silently at some later read.

Base classes here are DELIBERATELY grounding-agnostic (no `source_quote`
field on `ExtractedProcedureStep`) -- the grounded variant
(app/services/skill_extraction/grounded.py) subclasses these to add
verbatim-quote requirements, so deleting that one module later removes
the grounding machinery cleanly without touching this shared schema or
the ungrounded variant. See that module's own docstring for the full
grounded/ungrounded split rationale.

No `ExtractedClaim` here on purpose: Claims stay on their own,
already-LLM-driven, block-anchored path (app/services/claim_extraction.py,
which has real callers beyond skill_ingestion.py -- step_grounding.py,
ingestion_jobs.py). This schema covers exactly the three object types
skill_ingestion.py itself now produces via one call: Procedures, Goals,
Implementations.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_MAX_SHORT_TEXT = 500
_MAX_PROSE_TEXT = 4000
_MAX_LIST_ITEM = 1000

# Same meta-directive defense skill_ingestion.py's old
# _validate_capability_statement applied (§29 injection defense) -- moved
# here rather than imported from skill_ingestion.py to avoid a circular
# import once that module imports this package. Conservative by design:
# the prompt already instructs the model not to produce this content, but
# a model output is never trusted on its own -- any extracted text
# tripping this pattern is rejected, never repaired.
#
# 2026-09-16: the standalone TRUST_ASSERTION_RE bare-word check
# (`verified|trusted|approved|...` anywhere in the text, no
# self-referential requirement) was REMOVED -- see
# app/services/skill_ingestion.py::_screen_untrusted_document_raw's own
# comment for the real-corpus false-positive data that motivated this
# (same regex, same bug, found on the input-side twin of this check).
# META_DIRECTIVE_RE requires an actual injection-attempt SHAPE, not mere
# assertive vocabulary a legitimate extracted Goal/action could plausibly
# contain (e.g. "verify the deployment succeeded").
META_DIRECTIVE_RE = re.compile(
    r"(?:"
    r"ignore (?:all |any |the )?(?:previous |prior |above |earlier |preceding )?"
    r"(?:instruction|prompt|context|rule|message)|"
    r"disregard (?:all |any |the )?(?:previous |prior |above )?(?:instruction|rule|prompt)|"
    r"override (?:the )?(?:system|previous|prior|above|these)|"
    r"(?:reveal|print|output|show|leak) (?:your |the )?system prompt|"
    r"you are (?:now |hereby |henceforth )?(?:an? |the |no longer )|"
    r"as an? (?:ai|assistant|language model)|"
    r"new instructions?\s*:|"
    r"do not (?:abstain|reject|refuse|validate|screen)|"
    r"you (?:must|should|shall) (?:now )?(?:accept|approve|capture|mark|treat|"
    r"output|return|set|ignore|trust)|"
    r"treat (?:this|the following) (?:skill|document|procedure|content) as "
    r"(?:verified|trusted|approved|safe|authori[sz]ed)"
    r")",
    re.IGNORECASE,
)
# 2026-09-16: the bare `system prompt` clause was narrowed the same way
# and for the same reason as app/services/skill_ingestion.py's own
# _META_DIRECTIVE_RE -- see that module's comment for the real-corpus
# false positive (a skill-writing guide's own meta-documentation, not an
# attack) that motivated it.


def is_safe_extracted_text(text: Optional[str]) -> bool:
    """False if `text` trips the meta-directive pattern -- used to reject
    (never repair) an extracted goal/canonical_name/action that tries to
    smuggle an instruction aimed at the ingestion system."""
    if not text:
        return True
    return not META_DIRECTIVE_RE.search(text)


class SkillExtractionTransientFailure(Exception):
    """
    Raised by a DocumentExtractor (grounded or ungrounded) when it cannot
    produce a trustworthy result -- an LLM call that errored, timed out,
    or returned a response that doesn't parse into the expected shape.
    Mirrors app.services.procedure_extraction.schema.ExtractionTransientFailure
    exactly, including the "no silent fallback" discipline it exists to
    enforce: the caller (compile_skill_artifact) must let this propagate
    as a real, audited "rejected" outcome -- never fabricate a
    ParsedSkill-shaped substitute from raw frontmatter text (the actual
    bug this whole rearchitecture exists to close).

    Distinct from a genuine abstain (the model explicitly determined
    there is nothing extractable in this document) -- that is a real,
    final answer, not a failure, and a DocumentExtractor signals it by
    returning None from `extract()`, not by raising.
    """

    def __init__(self, message: str, *, is_rate_limit: bool = False):
        super().__init__(message)
        self.is_rate_limit = is_rate_limit


class ExtractedProcedureStep(BaseModel):
    order: int
    action: str = Field(min_length=1, max_length=_MAX_PROSE_TEXT)

    @field_validator("action")
    @classmethod
    def _action_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("step action must not be blank")
        return v


class ExtractedProcedure(BaseModel):
    """One reusable procedure this document expresses. A single document
    may express zero, one, or several distinct procedures (a runbook
    covering three independent operations, say) -- ExtractedDocument.
    procedures is a list for exactly this reason, not a single object;
    compile_skill_artifact() loops over it, one `procedures` row per
    entry, which is new plumbing this rearchitecture adds (previously
    one document could only ever produce one procedure)."""

    name: str = Field(min_length=1, max_length=_MAX_SHORT_TEXT)
    # The reusable-outcome sentence -- flows straight into
    # capture_procedure(goal=...) and, via that function's existing
    # wiring (app/services/goals.py::find_or_create_goal), becomes the
    # canonical Goal object's canonical_name. This is the exact field
    # the founding bug report was about: it must be a real abstracted
    # goal, never raw frontmatter/marketing text.
    goal: str = Field(min_length=1, max_length=_MAX_PROSE_TEXT)
    steps: list[ExtractedProcedureStep] = Field(default_factory=list)

    preconditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)

    # Optional, previously frontmatter-sourced display fields -- kept as
    # real (if sparse) extracted data rather than dropped outright, so
    # skill display pages don't silently regress.
    license: Optional[str] = Field(default=None, max_length=_MAX_SHORT_TEXT)
    compatibility: Optional[str] = Field(default=None, max_length=_MAX_SHORT_TEXT)
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("goal", "name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("steps")
    @classmethod
    def _steps_not_empty(cls, v: list[ExtractedProcedureStep]) -> list[ExtractedProcedureStep]:
        if not v:
            raise ValueError("a procedure with zero steps is not a procedure")
        return v

    @field_validator("preconditions", "failure_conditions", "postconditions", "exclusions", "allowed_tools")
    @classmethod
    def _list_items_bounded(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        for item in cleaned:
            if len(item) > _MAX_LIST_ITEM:
                raise ValueError(f"list item exceeds {_MAX_LIST_ITEM} chars")
        return cleaned


class ExtractedGoal(BaseModel):
    """A standalone Goal this document expresses that is not necessarily
    1:1 with one of `procedures` above -- e.g. a subgoal a step
    references, or a goal the document describes without itself
    supplying a full procedure for it. `canonical_name` maps directly
    onto app.services.goals.find_or_create_goal's own `canonical_name`
    parameter -- same field name, deliberately, so persistence is a
    direct pass-through, not a translation."""

    canonical_name: str = Field(min_length=1, max_length=_MAX_PROSE_TEXT)
    description: Optional[str] = Field(default=None, max_length=_MAX_PROSE_TEXT)
    expected_outcome: Optional[str] = Field(default=None, max_length=_MAX_PROSE_TEXT)
    verification_requirement: Optional[str] = Field(default=None, max_length=_MAX_PROSE_TEXT)

    @field_validator("canonical_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("canonical_name must not be blank")
        return v


# Closed vocabulary, mirrors the shape implementation_goals.py's own
# classification convention already established -- not exhaustive of
# every possible resource kind, but real, checked, and growable without
# a migration (it's a Python-side CHECK, not a DB enum, same split
# scope_type/kind already use elsewhere in this codebase).
IMPLEMENTATION_KINDS = frozenset({
    "script", "config", "workflow", "doc", "other",
})


class ExtractedImplementation(BaseModel):
    """One concrete mechanism this document bundles or describes.
    `resource_path` MUST reference a real, discovered file for this
    artifact (the actual bytes were hashed by github_corpus.py's
    deterministic file-tree scan -- a real fact, never LLM-invented);
    an extractor implementation validates this against the real
    discovered-path list post-parse and DROPS (never keeps) any entry
    that doesn't match, logging the discard rather than failing the
    whole document over one hallucinated path."""

    name: str = Field(min_length=1, max_length=_MAX_SHORT_TEXT)
    kind: str
    resource_path: str = Field(min_length=1, max_length=_MAX_SHORT_TEXT)
    # Maps directly onto implementations.goal (migration 80) -- also used,
    # when set, as the canonical_name find_or_create_goal resolves this
    # implementation's goal_id against. None is honest ("no goal could be
    # determined"), never a fabricated default -- same rule migration 80's
    # own docstring states for this column.
    goal: Optional[str] = Field(default=None, max_length=_MAX_PROSE_TEXT)
    expected_outcome: Optional[str] = Field(default=None, max_length=_MAX_PROSE_TEXT)

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if v not in IMPLEMENTATION_KINDS:
            raise ValueError(f"unknown implementation kind {v!r} (valid: {sorted(IMPLEMENTATION_KINDS)})")
        return v

    @field_validator("name", "resource_path")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class ExtractedDocument(BaseModel):
    """The whole structured result of extracting one document/artifact.
    Zero of any list is valid -- ingestion.md's own "zero objects is
    valid" rule, not every source expresses every object type. No
    top-level `claims` field (see module docstring)."""

    procedures: list[ExtractedProcedure] = Field(default_factory=list)
    goals: list[ExtractedGoal] = Field(default_factory=list)
    implementations: list[ExtractedImplementation] = Field(default_factory=list)
