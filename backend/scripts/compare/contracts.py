"""The records every Phase 2 script reads and writes (docs/goal_router.md P2-01, "Frozen interfaces").

RunRecord is frozen by the plan: P2-02..P2-07 import it, and it mirrors one task_runs row (db/191). Task and
ComparisonRecord are this milestone's; change them only with every consumer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

ARMS = ("procedure", "tool", "baseline")
STATUSES = ("pass", "fail", "error")
GOALS = ("web", "docs", "code_search", "packages", "security", "database")

# verifier kind -> the config keys it needs (scripts/compare/scoring.py, P2-04, implements each)
VERIFIERS: dict[str, tuple[str, ...]] = {
    "exact": ("value",),                         # normalized string equality
    "regex": ("pattern",),                       # re.search over the answer
    "contains_all": ("terms",),                  # every term present (case-insensitive by default)
    "sql_result": ("fixture_sql", "expected"),   # run the answer's SQL on a SQLite fixture; compare the rows
    "json_schema": ("schema",),                  # the answer parses as JSON and validates
    "test_suite": ("command",),                  # a test command decides (repo tasks)
    "rubric": ("rubric",),                       # a blind judge with a written rubric: open-ended tasks only
}
DATA_CLASSES = ("PUBLIC_SOURCE",)                # Phase 2 runs public data only


@dataclass
class RunRecord:
    run_id: str
    task_id: str
    arm: str               # "procedure" | "tool" | "baseline"
    goal_id: str | None
    procedure_id: str | None
    card_id: str | None
    started_at: str
    latency_ms: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    status: str            # "pass" | "fail" | "error"
    score: float
    artifact_hash: str | None
    error_class: str | None
    trace_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Task:
    id: str
    prompt: str
    goal_hint: str                        # one of GOALS
    verifier: dict[str, Any]              # {"kind": <VERIFIERS key>, ...its config}
    input_refs: list[str] = field(default_factory=list)
    data_class: str = "PUBLIC_SOURCE"
    budget_usd: float = 0.05              # hard cap per attempt
    tags: list[str] = field(default_factory=list)
    holdout: bool = False                 # kept out of tuning; reported separately

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonRecord:
    task_id: str
    goal_id: str | None
    procedure_id: str | None
    card_id: str | None
    n_pairs: int                          # attempts paired across the arms
    scores: dict[str, float]              # arm -> mean score
    pass_rates: dict[str, float]          # arm -> share of attempts with status "pass"
    paired_delta: float                   # mean(tool - procedure) over paired attempts
    cost_delta_usd: float                 # mean(tool - procedure)
    latency_delta_ms: float               # mean(tool - procedure)
    winner: str                           # "tool" | "procedure" | "tie" | "undecided" (never from a single pair)
    run_ids: list[str] = field(default_factory=list)
    evidence_ref: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
