"""Request and result types for execution."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    quality_bar: Optional[float] = None
    max_cost: Optional[float] = None


class StageResult(BaseModel):
    """One node's execution. Written to `traces` regardless of outcome."""

    node_ref: str
    task_node_id: Optional[UUID] = None
    task_name: str = ""
    implementation_id: Optional[UUID] = None
    implementation_name: str = ""
    implementation_kind: str = ""
    outcome: str = "failure"  # success | failure
    attempts: int = 1
    cache_hit: bool = False
    cost: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None
    output: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    run_id: Optional[UUID] = None
    status: str = "pending"
    stages: list[StageResult] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    @property
    def total_cost(self) -> float:
        return sum(s.cost for s in self.stages)

    @property
    def total_latency_ms(self) -> int:
        return sum(s.latency_ms for s in self.stages)


class RunSummary(BaseModel):
    id: UUID
    request_text: str
    status: str
    plan: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    stages: list[StageResult] = Field(default_factory=list)
    total_cost: float = 0.0
    total_latency_ms: int = 0
