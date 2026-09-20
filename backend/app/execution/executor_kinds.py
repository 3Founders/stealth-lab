"""Executor-KIND vocabulary + registry (which kinds of executor this process can actually run).

This is runtime plumbing, not a knowledge object: there is no Implementation entity. A step's `binding`
(procedures.steps[i].binding, see services/source_locators.py) names how the step is executed; `step_binding.py`
maps a binding kind to one of the executor kinds below, and this module says which of those have a real executor:

  - "frontier"      a frontier-model call (tier-1 completion or the sandboxed Agent tool loop) -- the ONE kind
                    with a registered strategy here, and the default for a step with no binding.
  - "slm"           a small/cheap model call (representable, no executor yet)
  - "deterministic" a fixed script/function (LocalAdapter sandbox in adapters.py)
  - "tool"          a named external tool / MCP call (McpToolAdapter)
  - "human"         a human-in-the-loop step

A kind outside the closed vocabulary is rejected at the boundary; a representable kind with no executor is
reported honestly as unsupported, never silently substituted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

EXECUTOR_KINDS: tuple[str, ...] = (
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

# A step with no binding keeps today's actual
# behavior: every existing run_node closure just runs its one real
# mechanism, which the registry itself must therefore treat as "frontier"
# was implicitly requested -- never as "nothing was requested, do
# nothing."
DEFAULT_KIND = "frontier"


class ExecutorKindViolation(ValueError):
    """A step names an implementation kind outside the closed vocabulary.
    Callers surface this verbatim -- producer-side contract violation,
    same discipline as V0Violation/PlanViolation, not an internal error."""


def validate_executor_hint(
    raw: Optional[Union[str, Sequence[str]]],
) -> Optional[tuple[str, ...]]:
    """The real gate: `raw` is whatever a stored step's
    `binding`-derived hint carries -- a single kind string, an
    ordered list of acceptable kinds (most-preferred first), or absent
    entirely. Returns a normalized, non-empty tuple, or None when the
    step named no hint at all (the common case today -- every stored
    procedure predates this field). ANY kind outside
    `EXECUTOR_KINDS` is rejected here, not silently accepted or
    silently dropped."""
    if raw is None:
        return None
    if isinstance(raw, str):
        kinds: tuple[str, ...] = (raw,)
    else:
        kinds = tuple(raw)
    if not kinds:
        raise ExecutorKindViolation(
            "V-IMPL: an executor hint, if present, must name at least one kind"
        )
    for kind in kinds:
        if not isinstance(kind, str) or kind not in EXECUTOR_KINDS:
            raise ExecutorKindViolation(
                f"V-IMPL: unknown implementation kind {kind!r} "
                f"(valid: {EXECUTOR_KINDS})"
            )
    return kinds


@dataclass(frozen=True)
class ExecutorResolution:
    """What the registry hands back for one node's (already-validated)
    executor hint. `supported=True` means a real executor exists
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


def resolve_executor(
    hint: Optional[tuple[str, ...]],
) -> ExecutorResolution:
    """Given a node's already-validated executor hint (or None),
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
            return ExecutorResolution(
                kind=kind, supported=True, strategy=strategy,
                reason=f"real executor registered for {kind!r}",
            )
    return ExecutorResolution(
        kind=candidates[0],
        supported=False,
        strategy=None,
        reason=(
            f"no real executor registered for any of {candidates!r} "
            f"(implemented today: {tuple(_REGISTERED_STRATEGIES)})"
        ),
    )
