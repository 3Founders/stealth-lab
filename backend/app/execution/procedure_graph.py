"""
Pure conversion: a stored Procedure's flat, ordered `steps` list into a
real, executable linear-dependency node list.

Why linear deps need no schema change: `steps[i]` is already ordered by
`order`, and db/18_procedures.sql's own DDL comment states steps "never
carries deps/requires scheduling fields" -- every procedure in the real
corpus today is linear by construction, not by omission. `deps=[i-1]` is
therefore recoverable directly from `order`, not invented or assumed.

Real branching (a step that forks into alternatives) would need an actual
schema addition -- named, not attempted here, in
.scratch/research/global-procedural-memory-architecture-audit-2026-08-30.md
section D. Nothing in the current corpus needs it, so it isn't built
speculatively.
"""
from __future__ import annotations

from app.models.plan import PlanNode


def steps_to_linear_nodes(steps: list[dict]) -> list[PlanNode]:
    """`steps`: a procedure's stored steps, each `{"order": int, "goal": str, ...}`
    (extra keys ignored). Returns PlanNodes in order, `deps=[i-1]` for
    i > 0, `[]` for the first -- a straight chain, matching exactly what
    a linear procedure already is."""
    ordered = sorted(steps, key=lambda s: s["order"])
    return [
        PlanNode(
            order=s["order"], goal=s["goal"],
            deps=[s["order"] - 1] if s["order"] > 0 else [],
        )
        for s in ordered
    ]
