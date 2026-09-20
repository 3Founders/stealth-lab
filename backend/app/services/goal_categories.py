"""Broad goal-category vocabulary and the default verification contract for bound (deterministic) steps.

Survivors of the retired implementation-goal classifier: a small literal taxonomy used as a hint by
step_grounding.py's prompt and by skill ingestion when it derives a step's goal from a bundled script path.
The vocabulary is a hint, never a closed enum -- goal names stay free text.
"""
from __future__ import annotations

import re
from typing import Optional

_GOAL_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\btest"), "test_execution"),
    (re.compile(r"\b(check|verify|validate)"), "verification"),
    (re.compile(r"\b(scan|audit|lint)"), "static_analysis"),
    (re.compile(r"\bmigrat"), "schema_migration"),
    (re.compile(r"\b(deploy|release)"), "deployment"),
    (re.compile(r"\bbuild"), "build"),
    (re.compile(r"\b(generate|codegen|scaffold)"), "code_generation"),
    (re.compile(r"\b(backfill|reindex)"), "data_backfill"),
)

KNOWN_GOAL_CATEGORIES: tuple[str, ...] = tuple(goal for _, goal in _GOAL_PATTERNS)

VERIFICATION_CONTRACT_TYPES: tuple[str, ...] = ("deterministic", "test", "external_system", "llm", "human")

_SEPARATOR_RE = re.compile(r"[_\-.]")


def normalize_goal_from_path(resource_path: str) -> Optional[str]:
    """A narrow deterministic classification from a resource's basename (never a guess); separators are
    normalized to spaces first so `run_tests.py` matches `\\btest`."""
    basename = resource_path.rsplit("/", 1)[-1].lower()
    normalized = _SEPARATOR_RE.sub(" ", basename)
    for pattern, goal in _GOAL_PATTERNS:
        if pattern.search(normalized):
            return goal
    return None


def default_verification_contract(kind: str) -> Optional[dict]:
    """A `deterministic` step (script/CLI invocation) has exactly one universally true success signal: it ran
    without erroring. Any richer contract needs real evidence and is never invented."""
    if kind == "deterministic":
        return {"type": "deterministic", "check": "exit_code_zero"}
    return None
