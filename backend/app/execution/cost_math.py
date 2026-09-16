"""
Pure, dependency-free cost arithmetic (Prompt 2 Sec 11, 2026-09-16).

Extracted out of `goal_cost.py` so this exact math has ONE derivation,
not two: `goal_cost.py` needs it for disclosed cost *estimation*, and
`implementation_selection.py` needs it for cost-informed *ranking* --
but `goal_cost.py` imports `goal_resolution.py`, which imports
`implementation_selection.py`, so `implementation_selection.py` cannot
import `goal_cost.py` directly (that would be circular). This module
has zero app imports, so both sides can depend on it.
"""
from __future__ import annotations

from typing import Optional


def expected_attempts(success_rate: Optional[float]) -> Optional[float]:
    """1 / success_rate -- the real geometric-distribution expectation
    of attempts-to-success. `None` when `success_rate` is `None` or
    exactly `0` -- an implementation with a 0% observed success rate has
    an undefined, not infinite-but-real, expected-attempts figure,
    reported honestly as `None` rather than a fabricated huge number."""
    if not success_rate:
        return None
    return 1.0 / success_rate


def expected_value(mean: Optional[float], attempts: Optional[float]) -> Optional[float]:
    """`mean * attempts`, or `None` if either input is unknown -- never
    a partial/fabricated estimate from only one of the two real
    quantities."""
    if mean is None or attempts is None:
        return None
    return mean * attempts
