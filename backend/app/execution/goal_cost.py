"""
Real, empirical Goal cost estimation (execu.md Sec 13/14). The founder's
own instruction on the disclosed gap this closes: "don't say we can
estimate cause we can't -- after some runs and accumulation of evidence
we will, bake this into the system." `execution_telemetry.py` is the
baking-in of the ledger; this module is the baking-in of the ESTIMATE
built from it.

Sec 13's own formula, implemented for real, recursively:

    ExpectedCost(G, I, context)
        = execution cost + expected retry/failure cost + verification cost

    ExpectedCost(G via P)
        = sum expected child Goal costs
        + orchestration overhead + verifier costs + expected fallback/retry costs

"execution cost" and "expected retry/failure cost" are both real here:
mean per-attempt cost (wall time / tokens) from `execution_telemetry.py`'s
real ledger, multiplied by the real expected-attempts-to-success factor
(`1 / success_rate`, the standard geometric-distribution expectation --
a real formula, not invented). "verification cost" and "orchestration
overhead" are NOT modeled yet (no real data source for either exists) --
left at 0 explicitly, never silently folded into another number, and
`basis` always says so.

`monetary_cost_usd` is ALWAYS `None` -- there is no pricing table
anywhere in this codebase (confirmed: `durable_run.py::record_run_usage`'s
own docstring already says "cost_usd stays 0 here -- an honest 'not
tracked'"). This module holds that same line rather than inventing one.

Confidence is graded by real sample count, not asserted:
  - "none": zero recorded executions for this Implementation (or the
    Goal is unresolved -- nothing to execute, nothing to cost).
  - "low": 1-4 real recorded executions -- a real number, but too few
    to trust as a stable estimate.
  - "empirical": 5+ real recorded executions.
These thresholds are a real, disclosed judgement call (Sec 14: "Do not
build an ML model yet... use empirical statistics"), not derived from
anything deeper -- revisit with real data once enough accumulates to
judge whether 5 is the right cutoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import asyncpg

from app.execution.cost_math import expected_attempts, expected_value
from app.execution.execution_telemetry import implementation_execution_stats
from app.execution.goal_resolution import ResolvedGoalNode

Confidence = Literal["none", "low", "empirical"]

_EMPIRICAL_THRESHOLD = 5


def _confidence(sample_count: int) -> Confidence:
    if sample_count >= _EMPIRICAL_THRESHOLD:
        return "empirical"
    if sample_count >= 1:
        return "low"
    return "none"


@dataclass
class CostEstimate:
    confidence: Confidence = "none"
    sample_count: int = 0
    success_rate: Optional[float] = None
    # Expected attempts-to-success (1/success_rate, the real geometric-
    # distribution expectation) -- the "expected retry/failure cost"
    # multiplier Sec 13 asks for, applied to every per-attempt quantity
    # below. None when success_rate is unknown or exactly 0 (an
    # implementation with a 0% observed success rate has an undefined,
    # not infinite-but-real, expected-attempts figure -- reported
    # honestly as None, never a fabricated huge number).
    expected_attempts: Optional[float] = None
    mean_wall_seconds: Optional[float] = None
    expected_wall_seconds: Optional[float] = None
    mean_prompt_tokens: Optional[float] = None
    expected_prompt_tokens: Optional[float] = None
    mean_completion_tokens: Optional[float] = None
    expected_completion_tokens: Optional[float] = None
    # Always None -- no pricing table exists anywhere in this codebase
    # (see module docstring). Never fabricated.
    monetary_cost_usd: Optional[float] = None
    # Sec 13's own named-but-unmodeled terms -- explicit 0, never folded
    # silently into another field.
    verification_cost_seconds: float = 0.0
    orchestration_overhead_seconds: float = 0.0
    basis: str = ""


async def estimate_implementation_cost(pool: asyncpg.Pool, implementation_id: str) -> CostEstimate:
    """`ExpectedCost(G, I, context)` for a single, already-chosen
    Implementation leaf -- real per-attempt means from
    `execution_telemetry.py`'s real ledger, scaled by the real expected-
    attempts-to-success factor."""
    stats = await implementation_execution_stats(pool, implementation_id)
    confidence = _confidence(stats.sample_count)
    if stats.sample_count == 0:
        return CostEstimate(
            confidence="none", sample_count=0,
            basis=f"no recorded executions yet for implementation {implementation_id}",
        )

    attempts = expected_attempts(stats.success_rate)

    basis = (
        f"{stats.sample_count} real recorded execution(s) of this implementation, "
        f"{stats.success_count} succeeded (success_rate={stats.success_rate:.2f})"
        if stats.success_rate is not None else
        f"{stats.sample_count} real recorded execution(s), success rate unknown"
    )
    return CostEstimate(
        confidence=confidence, sample_count=stats.sample_count, success_rate=stats.success_rate,
        expected_attempts=attempts,
        mean_wall_seconds=stats.mean_wall_seconds,
        expected_wall_seconds=expected_value(stats.mean_wall_seconds, attempts),
        mean_prompt_tokens=stats.mean_prompt_tokens,
        expected_prompt_tokens=expected_value(stats.mean_prompt_tokens, attempts),
        mean_completion_tokens=stats.mean_completion_tokens,
        expected_completion_tokens=expected_value(stats.mean_completion_tokens, attempts),
        basis=basis,
    )


def _sum_optional(values: list[Optional[float]]) -> Optional[float]:
    """Sums real values -- returns `None` (never a partial/misleading
    sum) if ANY input is `None`, since "the total cost" is only honest
    when every real contributor is known."""
    if any(v is None for v in values):
        return None
    return sum(values)


async def estimate_goal_cost(pool: asyncpg.Pool, node: ResolvedGoalNode) -> CostEstimate:
    """`ExpectedCost(G via P)` -- the real recursive aggregation over an
    already-resolved Goal tree (`goal_resolution.resolve_goal`'s own
    output). A Goal resolving straight to an Implementation delegates to
    `estimate_implementation_cost`; an unresolved Goal has no route to
    execute and therefore no cost (`confidence="none"`, explicit, not
    silently 0); a Procedure's cost is the real sum of its real children's
    costs -- if ANY child has zero samples, the aggregate is honestly
    "none" too (a total is not trustworthy when one real segment of the
    route has never actually run), with `basis` naming which.
    """
    if node.chosen == "implementation":
        return await estimate_implementation_cost(pool, str(node.implementation["id"]))

    if node.chosen == "unresolved":
        return CostEstimate(
            confidence="none", sample_count=0,
            basis=f"goal {node.goal_name!r} is unresolved -- no route to execute, no cost to estimate",
        )

    if node.chosen == "procedure":
        child_estimates = [await estimate_goal_cost(pool, c) for c in node.children]
        if not child_estimates:
            return CostEstimate(confidence="none", sample_count=0, basis="procedure has no steps")

        none_children = [c for c, e in zip(node.children, child_estimates) if e.confidence == "none"]
        total_samples = sum(e.sample_count for e in child_estimates)
        confidence: Confidence = "none"
        if not none_children:
            confidence = "empirical" if all(e.confidence == "empirical" for e in child_estimates) else "low"

        wall = _sum_optional([e.expected_wall_seconds for e in child_estimates])
        prompt = _sum_optional([e.expected_prompt_tokens for e in child_estimates])
        completion = _sum_optional([e.expected_completion_tokens for e in child_estimates])

        basis = (
            f"sum of {len(child_estimates)} child goal cost(s), {total_samples} total real samples"
            if not none_children else
            f"{len(none_children)}/{len(child_estimates)} child goal(s) have zero recorded executions "
            f"({', '.join(c.goal_name for c in none_children)}) -- total cost is not trustworthy until they do"
        )
        return CostEstimate(
            confidence=confidence, sample_count=total_samples,
            expected_wall_seconds=wall, expected_prompt_tokens=prompt, expected_completion_tokens=completion,
            basis=basis,
        )

    raise AssertionError(f"unknown ResolvedGoalNode.chosen value: {node.chosen!r}")
