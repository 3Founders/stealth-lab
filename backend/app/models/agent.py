"""
Agent Store models (AGENT_STORE_PLAN.md).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

AgentSource = Literal["internal", "graph_derived", "user_submitted", "external_marketplace"]
ExecutionMode = Literal["local_skill", "remote_http", "graph_workflow"]
AgentReviewState = Literal[
    "ingested", "under_review", "pending_human_approval", "approved", "rejected"
]


class Agent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    source: AgentSource
    source_decomposition_id: Optional[UUID] = None
    execution_mode: ExecutionMode
    skill_ref: Optional[str] = None
    remote_config: Optional[dict[str, Any]] = None
    workflow_task_ids: Optional[list[UUID]] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    review_state: AgentReviewState = "ingested"
    runnable: bool = False
    created_by: Optional[str] = None


class AgentReview(BaseModel):
    """
    Mirrors `Layer1Result` field-for-field on purpose: this *is* a Layer 1
    result, just persisted against an agent under review rather than a
    debate candidate. Kept as a distinct model (not a subclass) since an
    agent review's `agent_id` and a debate result's `candidate_id` mean
    different things and shouldn't be papered over as interchangeable.
    """

    id: UUID = Field(default_factory=uuid4)
    agent_id: UUID
    fallacy_flags: list[dict[str, Any]] = Field(default_factory=list)
    constructive: bool = True
    groundedness_score: float = 0.0
    unresolved_cites: list[str] = Field(default_factory=list)
    structural_problems: list[str] = Field(default_factory=list)
    passed: bool = False
    notes: str = ""
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)
