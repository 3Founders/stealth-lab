"""
Success criteria evaluation.

Deterministic by construction. A task's `success_criteria` is a small
declarative document, not an expression to evaluate and not a prompt -- the
whole reason a six-stage chain holds together is that most stages are exact,
and "ask a model whether the output looks right" would undo that at every
stage it touched.

Supported keys, all optional:

    required_keys  ["rows"]              present and not None
    non_empty      ["rows"]              len() > 0
    min_count      {"regions": 1}        len() >= n
    max_count      {"errors": 0}         len() <= n
    equals         {"doc_type": "table"} exact match
    file_exists    ["path"]              the value is a path that exists

Unknown keys are reported rather than ignored: a criterion nobody evaluates
is worse than no criterion, because it reads as a check that is happening.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Sized

_KNOWN = ("required_keys", "non_empty", "min_count", "max_count", "equals", "file_exists")


def _size(value: Any) -> int | None:
    if isinstance(value, (str, bytes)) or isinstance(value, Sized):
        return len(value)
    return None


def evaluate_criteria(criteria: Mapping[str, Any], output: Mapping[str, Any]) -> list[str]:
    """Returns the criteria that failed; empty means the stage passed."""
    if not criteria:
        return []

    failures: list[str] = []

    for key in criteria:
        if key not in _KNOWN:
            failures.append(f"unknown success criterion '{key}' -- nothing evaluates it")

    for name in criteria.get("required_keys", []) or []:
        if output.get(name) is None:
            failures.append(f"required output '{name}' is missing")

    for name in criteria.get("non_empty", []) or []:
        size = _size(output.get(name))
        if size is None:
            failures.append(f"output '{name}' has no length, so non_empty cannot hold")
        elif size == 0:
            failures.append(f"output '{name}' is empty")

    for name, minimum in (criteria.get("min_count") or {}).items():
        size = _size(output.get(name))
        if size is None:
            failures.append(f"output '{name}' has no length, so min_count cannot hold")
        elif size < minimum:
            failures.append(f"output '{name}' has {size} items, need at least {minimum}")

    for name, maximum in (criteria.get("max_count") or {}).items():
        size = _size(output.get(name))
        if size is None:
            failures.append(f"output '{name}' has no length, so max_count cannot hold")
        elif size > maximum:
            failures.append(f"output '{name}' has {size} items, allowed at most {maximum}")

    for name, expected in (criteria.get("equals") or {}).items():
        actual = output.get(name)
        if actual != expected:
            failures.append(f"output '{name}' is {actual!r}, expected {expected!r}")

    for name in criteria.get("file_exists", []) or []:
        path = output.get(name)
        if not path or not os.path.exists(str(path)):
            failures.append(f"output '{name}' does not point at a file that exists ({path!r})")

    return failures
