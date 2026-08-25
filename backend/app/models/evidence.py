"""
Pydantic models mirroring db/24_evidence.sql (Band 1.9a).

Same bridge discipline as plan.py: the DB stores flat columns / JSONB
payloads; these models give them names, and from_row()/to_row() bridge
explicitly -- if you add a column, add it in both places.

Shape authority is spec v4 §11 ("Evidence"), §9b (direction and
independence_group as named aggregation inputs) and schema.md's
"Evidence [H]" section; repo conventions win where they differ, exactly
as recorded in the migration header (scope pair, ticket-13 context_key,
bi-temporal tombstones).

The contracts themselves live in app/execution/evidence.py
(validate_evidence and friends); these models are the inert shapes
those functions produce and consume.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Scope strings are validated at the boundary (v0_gate.SCOPE_TYPES);
# typed here only as documentation.
ScopeType = str

EvidenceType = Literal[
    "execution_result",
    "observation",
    "experiment",
    "benchmark",
    "document",
    "human_review",
    "external_source",
    "artifact",
    "reproduction",
]

TargetType = Literal["claim", "procedure", "implementation"]
Direction = Literal["supports", "contradicts"]

# executions.outcome's exact closed vocabulary -- reused, not re-spelled
# (migration 23's rule). The *Label alias exists so boundary code can
# introspect the values (get_args over an Optional yields the Literal
# itself, not its members).
OutcomeStatusLabel = Literal["success", "failure", "needs_rework"]
OutcomeStatus = Optional[OutcomeStatusLabel]

# Spec v4 §36's six causes + false_reuse (BAND0_REVIEW.md settled the
# seventh value); null until a failure.
_FailureClassValue = Literal[
    "procedure_wrong",
    "implementation_wrong",
    "environment_changed",
    "input_abnormal",
    "verification_wrong",
    "external_failure",
    "false_reuse",
]
FailureClass = Optional[_FailureClassValue]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Strength(BaseModel):
    """strength: {score, method} (§11). The method is not decoration --
    an unstated method invites every consumer to treat a bare float as
    signal (the same reasoning that kept observations confidence-free)."""

    score: float = Field(ge=0.0, le=1.0)
    method: str = Field(min_length=1)


class TargetRef(BaseModel):
    """What an evidence row is evidence FOR. Procedure targets carry
    their exact version -- invariant #2's discipline applied to
    evidence: no versionless procedure references, anywhere."""

    target_type: TargetType
    target_id: UUID
    target_version: Optional[int] = Field(default=None, ge=1)


class Evidence(BaseModel):
    """One [H] evidence row: append-only, supersede-by-tombstone."""

    id: UUID = Field(default_factory=uuid4)
    evidence_type: EvidenceType
    target: TargetRef
    direction: Direction

    strength: Strength
    independence_group: Optional[str] = None  # NULL = self-grouped
    context_key: Optional[str] = None         # ticket-13 distinct contexts

    source_id: Optional[UUID] = None   # → Source (table lands later)
    content_ref: Optional[UUID] = None  # → Artifact (table lands later)

    outcome_status: OutcomeStatus = None
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    failure_class: FailureClass = None

    # Recorded history is closer to Event/Trace than to a compiled
    # artifact, so -- unlike execution_plans (migration 23) -- this
    # stamp is optional: machine-produced rows carry it, witness rows
    # (human_review, documents) need not pretend otherwise. Non-blank
    # when present, enforced at the boundary.
    extractor_version: Optional[str] = None
    created_by: Optional[str] = None
    visibility: Literal["public", "private"] = "public"
    owner_id: Optional[str] = None
    scope_type: Optional[ScopeType] = None
    scope_entity_id: Optional[str] = None

    t_valid: datetime = Field(default_factory=_now)
    t_invalid: Optional[datetime] = None
    t_created: datetime = Field(default_factory=_now)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Evidence":
        return cls(
            id=row["id"],
            evidence_type=row["evidence_type"],
            target=TargetRef(
                target_type=row["target_type"],
                target_id=row["target_id"],
                target_version=row.get("target_version"),
            ),
            direction=row["direction"],
            strength=Strength(
                score=row["strength_score"], method=row["strength_method"]
            ),
            independence_group=row.get("independence_group"),
            context_key=row.get("context_key"),
            source_id=row.get("source_id"),
            content_ref=row.get("content_ref"),
            outcome_status=row.get("outcome_status"),
            success_criteria=row.get("success_criteria") or {},
            failure_class=row.get("failure_class"),
            extractor_version=row["extractor_version"],
            created_by=row.get("created_by"),
            visibility=row.get("visibility") or "public",
            owner_id=row.get("owner_id"),
            scope_type=row.get("scope_type"),
            scope_entity_id=row.get("scope_entity_id"),
            t_valid=row.get("t_valid") or _now(),
            t_invalid=row.get("t_invalid"),
            t_created=row.get("t_created") or _now(),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "evidence_type": self.evidence_type,
            "target_type": self.target.target_type,
            "target_id": self.target.target_id,
            "target_version": self.target.target_version,
            "direction": self.direction,
            "strength_score": self.strength.score,
            "strength_method": self.strength.method,
            "independence_group": self.independence_group,
            "context_key": self.context_key,
            "source_id": self.source_id,
            "content_ref": self.content_ref,
            "outcome_status": self.outcome_status,
            "success_criteria": self.success_criteria,
            "failure_class": self.failure_class,
            "extractor_version": self.extractor_version,
            "created_by": self.created_by,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "scope_type": self.scope_type,
            "scope_entity_id": self.scope_entity_id,
            "t_valid": self.t_valid,
            "t_invalid": self.t_invalid,
            "t_created": self.t_created,
        }
