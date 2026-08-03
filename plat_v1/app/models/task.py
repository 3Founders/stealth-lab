"""Persisted task graph types: what a row in the database looks like in Python."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.plan import ImplKind, NodeKind


class TaskNode(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    kind: NodeKind = "leaf"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    # Input properties that form this task's cache fingerprint. None = all.
    cache_key: Optional[list[str]] = None
    version: int = 1
    provenance: str = "company_ingested"
    t_valid: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "TaskNode":
        return cls(**{k: row[k] for k in row.keys() if k in cls.model_fields})


class Implementation(BaseModel):
    id: UUID
    task_node_id: UUID
    name: str
    kind: ImplKind
    spec: dict[str, Any] = Field(default_factory=dict)
    cost_estimate: float = 0.0
    latency_estimate_ms: int = 0
    enabled: bool = True

    @classmethod
    def from_row(cls, row) -> "Implementation":
        return cls(
            id=row["id"],
            task_node_id=row["task_node_id"],
            name=row["name"],
            kind=row["kind"],
            spec=row["spec"] or {},
            # NUMERIC comes back as Decimal; the router sorts on it and the
            # API serialises it, and neither wants a Decimal.
            cost_estimate=float(row["cost_estimate"] or 0),
            latency_estimate_ms=int(row["latency_estimate_ms"] or 0),
            enabled=bool(row["enabled"]),
        )


class Eval(BaseModel):
    id: UUID
    task_node_id: UUID
    name: str
    cases: list[dict[str, Any]] = Field(default_factory=list)
    scorer: str = "exact_match"
