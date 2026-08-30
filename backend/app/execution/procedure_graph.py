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


def _step_goal(step: dict) -> str:
    """Real corpora aren't uniform: rows written before steps carried a
    `goal` field use `action` instead (same legacy shape
    app/mcp_server/server.py::_render_step already defends against --
    found live, this pass, when this function first hit a real
    older-shaped procedure and raised a bare KeyError). Falls back to a
    stringified step rather than crashing on a shape this substrate has
    always tolerated elsewhere."""
    return step.get("goal") or step.get("action") or str(step)


def steps_to_linear_nodes(steps: list[dict]) -> list[PlanNode]:
    """`steps`: a procedure's stored steps, each `{"order": int, "goal": str, ...}`
    (extra keys ignored). Returns PlanNodes in order, each depending on
    the PREVIOUS element in sorted sequence -- a straight chain, matching
    exactly what a linear procedure already is.

    REAL BUG this fixed, found live against a real stored procedure:
    deriving deps as `order - 1` assumes `order` is contiguous, 0-indexed,
    gapless -- a real corpus is not guaranteed to be (some older rows are
    1-indexed, e.g.). Dependency is really "whichever step sorts
    immediately before this one", which is a POSITION relationship, not
    an arithmetic one on the `order` value itself.
    """
    ordered = sorted(steps, key=lambda s: s["order"])
    return [
        PlanNode(
            order=s["order"], goal=_step_goal(s),
            deps=[ordered[i - 1]["order"]] if i > 0 else [],
        )
        for i, s in enumerate(ordered)
    ]
