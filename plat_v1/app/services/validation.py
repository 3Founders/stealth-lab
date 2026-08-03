"""
JSON Schema validation of runtime values.

Distinct from `typecheck.py`, which compares two *schemas* structurally. This
compares a *value* to a schema, and runs on every stage's inputs and outputs.
"""
from __future__ import annotations

from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

# Compiled validators are reused across stages; building one per stage
# execution is measurable on a six-stage plan run in a loop.
_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}


def _validator(schema: Mapping[str, Any]) -> Draft202012Validator:
    import json

    key = json.dumps(schema, sort_keys=True, default=str)
    cached = _VALIDATOR_CACHE.get(key)
    if cached is None:
        cached = Draft202012Validator(dict(schema))
        _VALIDATOR_CACHE[key] = cached
    return cached


def validate_value(value: Any, schema: Mapping[str, Any], label: str = "value") -> list[str]:
    """
    Returns human-readable violations; empty means valid.

    An empty schema validates everything. That is not a loophole here --
    typecheck rejects empty schemas before a plan can ever be executed, so
    reaching this function with one means the schema came from a hand-written
    task node, where the author is the operator.
    """
    if not schema:
        return []
    try:
        validator = _validator(schema)
    except SchemaError as exc:
        return [f"{label}: schema is not valid JSON Schema ({exc.message})"]

    problems = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.path)
        where = f"{label}.{path}" if path else label
        problems.append(f"{where}: {error.message}")
    return problems
