"""
Plan types.

A plan is the unit that moves between the decomposer, the typechecker, the
proposal queue, and the executor. It is deliberately a pure data structure
with no database identity: refs are local strings, because at the point a
plan is produced most of its nodes do not exist yet. That is the whole point
of a proposal.

`existing_task_id` is how reuse is expressed -- a node that names one is a
pointer at a task already in the graph, and inherits its implementations
rather than declaring its own.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

EdgeType = Literal["REQUIRES", "PRODUCES", "DECOMPOSES_TO"]
NodeKind = Literal["leaf", "composite"]
ImplKind = Literal["command", "python", "model"]


class ImplementationSpec(BaseModel):
    """An implementation proposed alongside a new node, or registered by hand."""

    name: str
    kind: ImplKind
    spec: dict[str, Any] = Field(default_factory=dict)
    cost_estimate: float = 0.0
    latency_estimate_ms: int = 0
    enabled: bool = True


class PlanEdge(BaseModel):
    type: EdgeType
    source_ref: str
    target_ref: str


class Expansion(BaseModel):
    """The subgraph a composite node expands into."""

    nodes: list["PlanNode"] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)


class PlanNode(BaseModel):
    ref: str
    name: str
    description: str = ""
    kind: NodeKind = "leaf"
    # JSON Schema. Empty objects are rejected at typecheck -- an empty schema
    # is the model declining to commit, and it defeats every downstream check.
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    # Input properties that form this stage's cache fingerprint. None = all.
    # Carried on the plan so the executor can fingerprint without a lookup.
    cache_key: Optional[list[str]] = None
    existing_task_id: Optional[UUID] = None
    implementations: list[ImplementationSpec] = Field(default_factory=list)
    expansion: Optional[Expansion] = None


class Plan(BaseModel):
    nodes: list[PlanNode] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)
    # Names of inputs the caller supplies. Anything a node needs that is not
    # produced upstream has to appear here or the plan does not typecheck.
    external_inputs: list[str] = Field(default_factory=list)
    feasible: bool = True
    reasoning: str = ""

    def node_by_ref(self, ref: str) -> Optional[PlanNode]:
        return next((n for n in self.nodes if n.ref == ref), None)


Expansion.model_rebuild()
PlanNode.model_rebuild()
