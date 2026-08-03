"""
Eval scoring.

Runs every enabled implementation of a task against a case set and writes an
`eval_results` row per implementation. Those rows are what the router's
quality bar reads, so this is the only thing that ever makes routing more
than a cost sort.

Scorers are deterministic functions, not model judges. A model grading
another model's output is a reasonable thing to build and a bad thing to
build first: it would make the measurement that governs routing as unreliable
as the thing being measured.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from app.runners.base import RunContext, Runner, RunnerError

log = logging.getLogger(__name__)


def exact_match(output: dict[str, Any], expected: dict[str, Any]) -> float:
    return 1.0 if output == expected else 0.0


def subset_match(output: dict[str, Any], expected: dict[str, Any]) -> float:
    """
    Every key the case asserts must match; extra output keys are ignored.

    The usual scorer here. A stage that returns the right rows plus a
    diagnostic field has not failed, and writing every incidental field into
    every expected case makes the cases unmaintainable.
    """
    if not expected:
        return 1.0
    hits = sum(1 for key, value in expected.items() if output.get(key) == value)
    return hits / len(expected)


SCORERS = {"exact_match": exact_match, "subset_match": subset_match}


@dataclass
class ImplementationScore:
    implementation_id: UUID
    implementation_name: str
    score: float
    cost: float = 0.0
    latency_ms: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class EvalRun:
    eval_id: UUID
    task_node_id: UUID
    case_count: int
    scores: list[ImplementationScore] = field(default_factory=list)


class EvalService:
    def __init__(self, pool, runners: dict[str, Runner], workdir: Optional[Path] = None):
        self._pool = pool
        self._runners = runners
        self._workdir = workdir or Path("./artifacts/evals")

    async def run(self, eval_id: UUID) -> EvalRun:
        row = await self._pool.fetchrow(
            "SELECT id, task_node_id, cases, scorer FROM evals "
            "WHERE id = $1 AND t_invalid IS NULL",
            eval_id,
        )
        if row is None:
            raise LookupError(f"eval {eval_id} does not exist")

        cases = row["cases"] or []
        scorer = SCORERS.get(row["scorer"])
        if scorer is None:
            raise ValueError(
                f"unknown scorer '{row['scorer']}'. Available: {', '.join(sorted(SCORERS))}"
            )

        impl_rows = await self._pool.fetch(
            """
            SELECT id, task_node_id, name, kind, spec, cost_estimate,
                   latency_estimate_ms, enabled
            FROM implementations
            WHERE task_node_id = $1 AND enabled AND t_invalid IS NULL
            """,
            row["task_node_id"],
        )

        result = EvalRun(
            eval_id=eval_id, task_node_id=row["task_node_id"], case_count=len(cases)
        )
        self._workdir.mkdir(parents=True, exist_ok=True)

        for impl in impl_rows:
            score = await self._score_one(impl, cases, scorer)
            result.scores.append(score)
            await self._pool.execute(
                """
                INSERT INTO eval_results (implementation_id, eval_id, score, cost,
                                          latency_ms, detail)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                impl["id"],
                eval_id,
                score.score,
                score.cost,
                score.latency_ms,
                {"failures": score.failures[:20], "cases": len(cases)},
            )

        return result

    async def _score_one(self, impl, cases: list[dict], scorer) -> ImplementationScore:
        runner = self._runners.get(impl["kind"])
        score = ImplementationScore(
            implementation_id=impl["id"], implementation_name=impl["name"], score=0.0
        )
        if runner is None:
            score.failures.append(f"no runner for kind '{impl['kind']}'")
            return score
        if not cases:
            # No cases means no evidence, and no evidence must not read as a
            # perfect score -- the router would then treat it as the bar.
            score.failures.append("eval has no cases")
            return score

        total = 0.0
        for index, case in enumerate(cases):
            ctx = RunContext(
                workdir=self._workdir,
                node_ref=f"eval-{index}",
                task_name=impl["name"],
                output_schema={},
            )
            started = time.monotonic()
            try:
                run_result = await runner.run(dict(impl["spec"] or {}), case.get("input") or {}, ctx)
                total += scorer(run_result.output, case.get("expected") or {})
                score.cost += run_result.cost
            except RunnerError as exc:
                score.failures.append(f"case {index}: {exc}")
            except Exception as exc:  # noqa: BLE001
                score.failures.append(f"case {index}: {type(exc).__name__}: {exc}")
            score.latency_ms += int((time.monotonic() - started) * 1000)

        score.score = total / len(cases)
        return score
