"""Runner protocol and the context every runner receives."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import UUID


class RunnerError(Exception):
    """A runner failed. Caught by the executor, recorded, and escalated past."""


@dataclass
class RunContext:
    """
    Everything a runner is allowed to know about the surrounding run.

    Deliberately narrow: a runner gets a working directory, the schema it is
    expected to produce, and identifiers for tracing. It does not get the
    pool, the router, or the rest of the plan.
    """

    workdir: Path
    node_ref: str = ""
    task_name: str = ""
    output_schema: dict[str, Any] = field(default_factory=dict)
    run_id: Optional[UUID] = None
    # Parameters recalled from a cache hit, if any.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerResult:
    output: dict[str, Any]
    cost: float = 0.0
    # Written into the cache entry alongside the winning implementation, so a
    # later run on the same layout reuses not just the implementation but
    # whatever it had to work out the first time.
    params: dict[str, Any] = field(default_factory=dict)


class Runner(Protocol):
    kind: str

    async def run(
        self, spec: dict[str, Any], inputs: dict[str, Any], ctx: RunContext
    ) -> RunnerResult: ...
