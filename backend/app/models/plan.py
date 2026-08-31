"""
Pydantic models mirroring db/23_plan_persistence.sql (Band 1.7).

Same bridge discipline as ontology.py: the DB stores flat columns /
JSONB payloads; these models give them names, and from_row()/to_row()
bridges explicitly -- if you add a column, add it in both places.

Shape authority is schema.md's ExecutionPlan / TaskGraph / Execution
sections and spec v4 sections 15/22. Where schema.md renders something
as a presentation string ("procedure_42:v7"), the repo's native
convention wins: procedure_id + integer version as two typed fields
(the same choice migration 23 records in its header).

The freeze contracts themselves live in app/execution/plans.py
(canonical hashing, compile-time validation, rebind checks); these
models are the inert shapes those functions produce and consume.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Scope strings are validated at the boundary (v0_gate.SCOPE_TYPES);
# typed here only as documentation.
ScopeType = str

# Implementation-kind strings are validated at the boundary
# (app.execution.implementations.IMPLEMENTATION_KINDS /
# validate_implementation_hint), same discipline as ScopeType above --
# typed here only as documentation. A tuple, not a single str: a step may
# advertise several acceptable kinds in preference order.
ImplementationHint = tuple[str, ...]

SafetyCheck = Literal["passed", "failed", "requires_review"]
NodeClass = Literal["predictable", "uncertain", "high-risk"]
ExecutionOutcome = Literal["success", "failure", "needs_rework"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProcedureRef(BaseModel):
    """Invariant #2's exact-version reference -- both halves required.

    A versionless procedure reference (id without version) is not
    representable here: constructing one raises before any storage
    boundary is reached."""

    procedure_id: UUID
    version: int = Field(ge=1)


class ResolvedClaimRef(BaseModel):
    """A claim frozen into a plan with its exact version (spec 16
    discipline applied to plans: references, never embedded claims)."""

    claim_id: UUID
    version: int = Field(ge=1)


class PlanNode(BaseModel):
    """One TaskNode of a compiled graph (schema.md TaskNode).

    `deps` are scheduling edges and exist ONLY here (spec 24) -- there
    is no separate edge store for plans. Optional scope fields narrow
    the plan's scope; widening past it is rejected at compile time."""

    order: int = Field(ge=0)
    goal: str
    step_ref: Optional[ProcedureRef] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    node_class: NodeClass = "predictable"
    implementation_id: Optional[str] = None
    # Advisory sibling of `step_ref` (composition): which real
    # implementation kind(s) could satisfy this node's own work, once it
    # is an ordinary (non-composed) node. None means the step named no
    # preference -- app.execution.implementations.resolve_implementation
    # treats that as "frontier", matching every real caller's current
    # unconditional behavior. Never embeds an executor CHOICE -- only a
    # preference a registry/executor may honor, ignore, or reinterpret.
    implementation_hint: Optional[ImplementationHint] = None
    cost_budget: dict[str, Any] = Field(default_factory=dict)
    verification_gate: dict[str, Any] = Field(default_factory=dict)
    deps: list[int] = Field(default_factory=list)
    scope_type: Optional[ScopeType] = None
    scope_entity_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PlanNode":
        return cls(**row)


class TaskGraph(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    execution_plan_id: UUID
    graph_hash: str
    nodes: list[PlanNode] = Field(default_factory=list)
    created_by: Optional[str] = None
    visibility: Literal["public", "private"] = "public"
    owner_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "TaskGraph":
        return cls(
            id=row["id"],
            execution_plan_id=row["execution_plan_id"],
            graph_hash=row["graph_hash"],
            nodes=[PlanNode.from_row(n) for n in (row.get("nodes") or [])],
            created_by=row.get("created_by"),
            visibility=row.get("visibility") or "public",
            owner_id=row.get("owner_id"),
            created_at=row.get("created_at") or _now(),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "execution_plan_id": self.execution_plan_id,
            "graph_hash": self.graph_hash,
            "nodes": [n.model_dump(mode="json") for n in self.nodes],
            "created_by": self.created_by,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
        }


class ExecutionPlan(BaseModel):
    """The [D-to-frozen] compiled plan itself."""

    id: UUID = Field(default_factory=uuid4)
    procedure: ProcedureRef
    procedure_row_id: UUID  # the immutable procedures row the compile read
    task_graph_id: UUID

    scope_type: Optional[ScopeType] = None
    scope_entity_id: Optional[str] = None

    task_description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    starting_state_id: Optional[UUID] = None

    resolved_claims: list[ResolvedClaimRef] = Field(default_factory=list)
    selected_branches: list[str] = Field(default_factory=list)
    implementations: dict[str, str] = Field(default_factory=dict)

    safety_check: Optional[SafetyCheck] = None
    verification_plan: dict[str, Any] = Field(default_factory=dict)

    extractor_version: str
    procedure_content_hash: str
    content_hash: str

    created_by: Optional[str] = None
    visibility: Literal["public", "private"] = "public"
    owner_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ExecutionPlan":
        return cls(
            id=row["id"],
            procedure=ProcedureRef(
                procedure_id=row["procedure_id"], version=row["procedure_version"]
            ),
            procedure_row_id=row["procedure_row_id"],
            task_graph_id=row["task_graph_id"],
            scope_type=row.get("scope_type"),
            scope_entity_id=row.get("scope_entity_id"),
            task_description=row["task_description"],
            parameters=row.get("parameters") or {},
            starting_state_id=row.get("starting_state_id"),
            resolved_claims=[
                ResolvedClaimRef(**c) for c in (row.get("resolved_claims") or [])
            ],
            selected_branches=list(row.get("selected_branches") or []),
            implementations=row.get("implementations") or {},
            safety_check=row.get("safety_check"),
            verification_plan=row.get("verification_plan") or {},
            extractor_version=row["extractor_version"],
            procedure_content_hash=row["procedure_content_hash"],
            content_hash=row["content_hash"],
            created_by=row.get("created_by"),
            visibility=row.get("visibility") or "public",
            owner_id=row.get("owner_id"),
            created_at=row.get("created_at") or _now(),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "procedure_id": self.procedure.procedure_id,
            "procedure_version": self.procedure.version,
            "procedure_row_id": self.procedure_row_id,
            "task_graph_id": self.task_graph_id,
            "scope_type": self.scope_type,
            "scope_entity_id": self.scope_entity_id,
            "task_description": self.task_description,
            "parameters": self.parameters,
            "starting_state_id": self.starting_state_id,
            "resolved_claims": [c.model_dump(mode="json") for c in self.resolved_claims],
            "selected_branches": list(self.selected_branches),
            "implementations": self.implementations,
            "safety_check": self.safety_check,
            "verification_plan": self.verification_plan,
            "extractor_version": self.extractor_version,
            "procedure_content_hash": self.procedure_content_hash,
            "content_hash": self.content_hash,
            "created_by": self.created_by,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
        }


class Execution(BaseModel):
    """An [H] append-only run of an ExecutionPlan (invariant #1's
    subject: execution_plan_id has no nullable path)."""

    id: UUID = Field(default_factory=uuid4)
    execution_plan_id: UUID
    task_graph_id: UUID
    procedure: ProcedureRef  # denormalized exact-version snapshot (spec 22)

    state_id: Optional[UUID] = None
    implementation_id: Optional[UUID] = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    trace_id: Optional[str] = None
    started_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None
    outcome: Optional[ExecutionOutcome] = None

    actor_id: Optional[str] = None
    created_by: Optional[str] = None
    visibility: Literal["public", "private"] = "public"
    owner_id: Optional[str] = None
    scope_type: Optional[ScopeType] = None
    scope_entity_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Execution":
        return cls(
            id=row["id"],
            execution_plan_id=row["execution_plan_id"],
            task_graph_id=row["task_graph_id"],
            procedure=ProcedureRef(
                procedure_id=row["procedure_id"], version=row["procedure_version"]
            ),
            state_id=row.get("state_id"),
            implementation_id=row.get("implementation_id"),
            parameters=row.get("parameters") or {},
            trace_id=row.get("trace_id"),
            started_at=row.get("started_at") or _now(),
            ended_at=row.get("ended_at"),
            outcome=row.get("outcome"),
            actor_id=row.get("actor_id"),
            created_by=row.get("created_by"),
            visibility=row.get("visibility") or "public",
            owner_id=row.get("owner_id"),
            scope_type=row.get("scope_type"),
            scope_entity_id=row.get("scope_entity_id"),
            created_at=row.get("created_at") or _now(),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "execution_plan_id": self.execution_plan_id,
            "task_graph_id": self.task_graph_id,
            "procedure_id": self.procedure.procedure_id,
            "procedure_version": self.procedure.version,
            "state_id": self.state_id,
            "implementation_id": self.implementation_id,
            "parameters": self.parameters,
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "actor_id": self.actor_id,
            "created_by": self.created_by,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "scope_type": self.scope_type,
            "scope_entity_id": self.scope_entity_id,
            "created_at": self.created_at,
        }
