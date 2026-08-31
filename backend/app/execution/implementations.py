"""Implementation-KIND vocabulary + registry (product spec P1: "Complete
implementation abstraction").

Two concerns, deliberately kept in one small module because they are two
halves of the same gate, the same way v0_gate.py keeps SCOPE_TYPES and
validate_scope() together:

  1. A closed, validated vocabulary of implementation KINDS a procedure
     step may advertise as acceptable for satisfying it -- an axis
     ORTHOGONAL to `PlanNode.node_class` (risk classification: how
     dangerous is this node) and to `PlanNode.step_ref`/`subprocedure_ref`
     (composition: does this node dissolve into another procedure's
     steps). `implementation_hint` answers a third, independent question:
     what KIND of executor may satisfy this node's real work once it is
     an ordinary (non-composed) node.

  2. A real registry mapping a supported subset of that vocabulary to the
     one real executor this codebase actually has today, plus an honest
     "not yet implemented" signal for every kind that is representable
     but has no real executor wired up -- so a caller can never silently
     run the wrong thing, or silently do nothing, for a kind it doesn't
     support.

Vocabulary chosen (`IMPLEMENTATION_KINDS`) and why each member earns its
place, rather than being invented speculatively:

  - "frontier"     the ONE kind with a real executor today: a full
                    frontier-model call, either the tier-1 lightweight
                    completion closure or the tier-2/reproduce_procedure/
                    local_agent sandboxed Agent+tool-calling loop --
                    `server.py` and `runner.py` already do exactly this
                    for every node, unconditionally. Naming it makes the
                    status quo representable, not aspirational.
  - "slm"           a small/cheap local or hosted model call, distinct
                    from a frontier model on cost/latency/capability --
                    named because it is the spec's own explicit example
                    of a real, near-term differentiated implementation
                    kind, not built here (no real executor exists yet).
  - "deterministic" a fixed script/function, no model call at all --
                    the cheapest, most auditable kind when a step's work
                    is genuinely mechanical (e.g. "run the linter").
  - "tool"          a specific named external tool/API call, routed by
                    name rather than reasoned about -- distinct from
                    "deterministic" (a step names WHICH external tool,
                    not an inline function) and already has a real data
                    precedent in stored steps: `allowed_implementations`
                    (see procedure_graph.py's `_render_step` handling and
                    test_procedure_graph_offline.py's
                    test_extra_step_fields_are_ignored).
  - "human"         a human-in-the-loop step -- named because spec v4
                    and BAND0_DECISIONS.md both treat human approval as
                    a real, first-class execution path for high-risk
                    work, not something this substrate would ever
                    silently substitute a model call for.

Only "frontier" is executable today. That is not a placeholder choice --
it is the literal, current, entire real-execution capability of this
codebase (confirmed by reading graph_executor.py, server.py's two
run_node closures, and runner.py's `_run_local_node` in full before this
module was written: zero behavioral branching on any implementation axis
exists anywhere today). The other four kinds are real, validated,
storable, and queryable -- and honestly reported as unimplemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

IMPLEMENTATION_KINDS: tuple[str, ...] = (
    "deterministic", "tool", "slm", "frontier", "human",
)

# The one kind with a real, already-built executor behind it today --
# `frontier` covers both real closures this pass read in full: server.py's
# tier-1 lightweight completion call and the tier-2/reproduce_procedure/
# local_agent sandboxed Agent+tool-calling loop. Both are "call a frontier
# model to do real work"; neither is distinguished further here because
# nothing in the codebase branches on that distinction today either.
_REGISTERED_STRATEGIES: dict[str, str] = {
    "frontier": "frontier_model_call",
}

# A step that names no implementation_hint at all keeps today's actual
# behavior: every existing run_node closure just runs its one real
# mechanism, which the registry itself must therefore treat as "frontier"
# was implicitly requested -- never as "nothing was requested, do
# nothing."
DEFAULT_KIND = "frontier"


class ImplementationViolation(ValueError):
    """A step names an implementation kind outside the closed vocabulary.
    Callers surface this verbatim -- producer-side contract violation,
    same discipline as V0Violation/PlanViolation, not an internal error."""


def validate_implementation_hint(
    raw: Optional[Union[str, Sequence[str]]],
) -> Optional[tuple[str, ...]]:
    """The real gate: `raw` is whatever a stored step's
    `implementation_hint` field carries -- a single kind string, an
    ordered list of acceptable kinds (most-preferred first), or absent
    entirely. Returns a normalized, non-empty tuple, or None when the
    step named no hint at all (the common case today -- every stored
    procedure predates this field). ANY kind outside
    `IMPLEMENTATION_KINDS` is rejected here, not silently accepted or
    silently dropped."""
    if raw is None:
        return None
    if isinstance(raw, str):
        kinds: tuple[str, ...] = (raw,)
    else:
        kinds = tuple(raw)
    if not kinds:
        raise ImplementationViolation(
            "V-IMPL: implementation_hint, if present, must name at least one kind"
        )
    for kind in kinds:
        if not isinstance(kind, str) or kind not in IMPLEMENTATION_KINDS:
            raise ImplementationViolation(
                f"V-IMPL: unknown implementation kind {kind!r} "
                f"(valid: {IMPLEMENTATION_KINDS})"
            )
    return kinds


@dataclass(frozen=True)
class ImplementationResolution:
    """What the registry hands back for one node's (already-validated)
    implementation_hint. `supported=True` means a real executor exists
    for `kind` and a caller may proceed exactly as it does today;
    `supported=False` means every candidate kind is real and valid but
    has no real executor wired up -- callers MUST treat this as an
    explicit, actionable signal (skip, refuse, or fall back to a
    supported kind by their own policy), never as license to run
    something anyway or to silently no-op."""

    kind: str
    supported: bool
    strategy: Optional[str]
    reason: str


def resolve_implementation(
    hint: Optional[tuple[str, ...]],
) -> ImplementationResolution:
    """Given a node's already-validated implementation_hint (or None),
    return which real strategy -- if any -- satisfies it.

    `hint` is a preference-ordered tuple: the first candidate with a real
    registered strategy wins. A bare `None` (no hint at all) resolves to
    `DEFAULT_KIND` ("frontier") -- the advisory absence of a preference,
    not a request for "no implementation", matching every real caller's
    current unconditional behavior for hint-less nodes.

    This function embeds no executor choice into stored data -- it only
    reads a node's advisory hint and reports what THIS process's current
    registry can do about it. A future/different executor is free to
    register more strategies (or none) and reinterpret the same stored
    hint differently; the procedure itself never hardcodes the decision
    (spec's planner/runtime-neutral rule)."""
    candidates = hint if hint else (DEFAULT_KIND,)
    for kind in candidates:
        strategy = _REGISTERED_STRATEGIES.get(kind)
        if strategy is not None:
            return ImplementationResolution(
                kind=kind, supported=True, strategy=strategy,
                reason=f"real executor registered for {kind!r}",
            )
    return ImplementationResolution(
        kind=candidates[0],
        supported=False,
        strategy=None,
        reason=(
            f"no real executor registered for any of {candidates!r} "
            f"(implemented today: {tuple(_REGISTERED_STRATEGIES)})"
        ),
    )
