"""
Registered Python callables.

    {"ref": "tables:extract_cell_structure"}

The ref is resolved against an explicit registry dict. It is **never** passed
to `importlib` -- a database value that names a module to import is arbitrary
code execution with extra steps, and the fact that only the operator can
write to this database today is not a reason to build it that way.

An unknown ref lists what is registered, because the failure is almost always
a typo or an implementation registered under a different name.
"""
from __future__ import annotations

import inspect
from typing import Any

from app.runners.base import RunContext, RunnerError, RunnerResult
from app.runners.registry import REGISTRY


class PythonRunner:
    kind = "python"

    def __init__(self, registry: dict[str, Any] | None = None):
        self._registry = REGISTRY if registry is None else registry

    async def run(
        self, spec: dict[str, Any], inputs: dict[str, Any], ctx: RunContext
    ) -> RunnerResult:
        ref = spec.get("ref")
        if not ref:
            raise RunnerError("python implementation has no 'ref'")

        fn = self._registry.get(ref)
        if fn is None:
            known = ", ".join(sorted(self._registry)) or "(registry is empty)"
            raise RunnerError(f"unknown python ref '{ref}'. Registered: {known}")

        try:
            result = fn(inputs, ctx)
            if inspect.isawaitable(result):
                result = await result
        except RunnerError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Any exception is a stage failure, not a crash: the executor
            # records it and escalates to the next implementation, which is
            # the entire reason a cheap deterministic attempt goes first.
            raise RunnerError(f"{ref} raised {type(exc).__name__}: {exc}") from exc

        if isinstance(result, RunnerResult):
            return result
        if not isinstance(result, dict):
            raise RunnerError(
                f"{ref} returned {type(result).__name__}; a python implementation must "
                f"return a dict of outputs"
            )
        return RunnerResult(output=result)
