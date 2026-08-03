"""
Prompt in, candidate match out.

A match is accepted automatically only when both hold:

  1. the top result's fused retrieval score exceeds the threshold, and
  2. the caller's supplied inputs validate against that task's input_schema

The second is the real gate. Semantic similarity will happily match "extract
tables from a PDF" to "extract text from a PDF" -- they are the same sentence
to an embedding model and completely different tasks. Schema validation is
what distinguishes them, because the one that returns a grid of cells and the
one that returns a string have different contracts and only one of them fits
what the caller is holding.

Failing the gate is not an error. It falls through to decomposition, which is
the slower path that ends with a human looking at a plan.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings
from app.services.matching import Match, TaskMatcher
from app.services.validation import validate_value

log = logging.getLogger(__name__)


@dataclass
class Intake:
    prompt: str
    candidates: list[Match] = field(default_factory=list)
    accepted: Optional[Match] = None
    reason: str = ""
    schema_problems: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.accepted is not None


class IntakeService:
    def __init__(self, matcher: TaskMatcher, threshold: Optional[float] = None):
        self._matcher = matcher
        self._threshold = (
            settings.auto_match_threshold if threshold is None else threshold
        )

    async def assess(self, prompt: str, inputs: dict[str, Any]) -> Intake:
        candidates = await self._matcher.search(prompt, top_k=5)
        result = Intake(prompt=prompt, candidates=candidates)

        if not candidates:
            result.reason = "no existing task resembled the request"
            return result

        top = candidates[0]
        if top.score <= self._threshold:
            result.reason = (
                f"best match '{top.task.name}' scored {top.score:.4f}, at or below the "
                f"auto-match threshold {self._threshold:.4f}"
            )
            return result

        problems = validate_value(inputs, top.task.input_schema, "inputs")
        if problems:
            result.schema_problems = problems
            result.reason = (
                f"'{top.task.name}' looked right ({top.score:.4f}) but the supplied inputs "
                f"do not satisfy its input schema: {problems[0]}"
            )
            log.info("match rejected on schema: %s", result.reason)
            return result

        result.accepted = top
        result.reason = f"matched '{top.task.name}' at {top.score:.4f}"
        return result
