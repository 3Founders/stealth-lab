"""
The extraction contract (procedure extraction, memory-substrate map).
Mirrors procedures.py's real columns so persistence is a field mapping,
not a translation -- see __init__.py's extract_procedure() for the
mapping itself.

Strict Pydantic, not a plain dict: a malformed extraction result must
fail loudly at the schema boundary, not silently at some later read
(the same discipline claims.py's ClaimProperties already established
for this codebase).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Predicate(BaseModel):
    """One {subject, predicate, object} triple -- the exact shape
    applicability.py's check_hard_constraints() reads back out of
    procedures.preconditions."""
    subject: str
    predicate: str
    object: Optional[str] = None


class SlotSpec(BaseModel):
    """`name` is the placeholder a step references as `{name}`;
    `binder` must name a producer registered in slot_binders.py --
    validators.py's V3 checks this, not this model (Pydantic validates
    shape, not cross-references to a runtime registry)."""
    name: str
    binder: str
    description: str = ""


class ProcedureStep(BaseModel):
    """
    Planner-neutral by construction: NO deps/requires field exists on
    this model at all, which is what makes V2 (step purity) a structural
    guarantee rather than a runtime check that could be bypassed --
    there is nowhere on this type to put a scheduling edge. Ticket 05:
    "steps on the procedure is planner-neutral -- the HTN-specific
    binding (ticket 15) translates this into a concrete DAG at
    instantiation time."
    """
    order: int
    action: str

    @field_validator("action")
    @classmethod
    def action_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("step action must not be empty")
        return v


class ExtractedProcedure(BaseModel):
    name: str
    goal: str
    # The abstract, domain-stripped field -- NOT `goal`. This is what
    # gets embedded for retrieval; V4 (capability abstraction) enforces
    # that it names nothing concrete from the source evidence. See
    # migration 20's own comment on procedures.capability_statement for
    # why this must be a distinct field, not a convention inside `goal`.
    capability_statement: str

    steps: list[ProcedureStep] = Field(default_factory=list)
    slots: list[SlotSpec] = Field(default_factory=list)

    preconditions: list[Predicate] = Field(default_factory=list)
    scope: dict = Field(default_factory=dict)
    exclusions: list[dict] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def steps_not_empty(cls, v: list[ProcedureStep]) -> list[ProcedureStep]:
        if not v:
            raise ValueError("a procedure with zero steps is not a procedure")
        return v


class ExtractionResult(BaseModel):
    """Returned by extract_procedure() -- either a persisted procedure
    id, or a validation failure, never both. Structured rather than a
    bare Optional so a caller can distinguish "nothing extractable"
    (V5, evidence insufficiency -- expected, not an error) from "the
    strategy produced something malformed" (a real bug worth seeing)."""
    model_config = {"arbitrary_types_allowed": True}

    procedure_id: Optional[str] = None
    version_row_id: Optional[str] = None
    extracted_by: Optional[str] = None
    extracted: Optional[ExtractedProcedure] = None
    validation_failures: list[str] = Field(default_factory=list)
    used_fallback: bool = False

    @property
    def succeeded(self) -> bool:
        return self.procedure_id is not None and not self.validation_failures
