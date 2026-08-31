"""
Pure conversion: a stored Procedure's flat, ordered `steps` list into a
real, executable linear-dependency node list -- PLUS (Phase 10) real
procedure composition: a step can pin an exact-version reference to
another procedure, and this module is what splices that referenced
procedure's own steps into the parent graph at compile time.

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

--- Phase 10: procedure composition ---

Design choice (documented here, not just in the handoff report): a step
that references a sub-procedure carries this shape inline in the existing
`steps` JSONB array --

    {"order": 2, "goal": "run the shared lint pass",
     "subprocedure_ref": {"procedure_id": "<uuid>", "version": 3}}

-- losslessly representable in the existing column, so NO migration is
added. `subprocedure_ref` mirrors `ProcedureRef` (app/models/plan.py)
exactly: both halves required, exact integer version, never "latest" --
the same invariant #2 discipline plans.py already enforces for a plan's
own top-level procedure reference, applied here to a STEP's reference.

`steps_to_linear_nodes()` stays a pure, single-level conversion (used
unmodified today by mcp_server/server.py and local_agent/runner.py for
non-composed procedures) and now also lifts `subprocedure_ref` into
`PlanNode.step_ref` when present, so a caller who does NOT expand still
gets an honest, typed signal that a step is a reference rather than real
work.

`expand_procedure_steps()` is the real compile-time expansion: given a
root procedure's steps, it calls `steps_to_linear_nodes()` for the
current level, then for every node carrying a `step_ref` it fetches the
EXACT pinned version (never a superseding one), recursively expands that
version's own steps, and splices the resulting subgraph into the parent
at the referencing node's position -- the reference node itself dissolves
into its expansion; nothing downstream should be able to distinguish a
composed graph from a hand-authored one of the same shape (`deps` chains
correctly rewired, orders renumbered contiguous and unique). This keeps
`app/execution/plans.py::compile_plan` untouched and pure/pool-free
(its own documented invariant) -- expansion is a real async, DB-touching
step a caller runs BEFORE calling `compile_plan(nodes=expanded)`.

Cycle/recursion safety (two independent nets, per the spec's explicit
"prevent cycles, infinite recursion, version ambiguity"):
  1. `chain` tracks every (procedure_id, version) pair currently being
     expanded on the current path; a step whose pinned ref repeats one
     already in `chain` raises `ProcedureCompositionCycle` -- refused,
     never silently truncated.
  2. `max_depth` (default `DEFAULT_MAX_COMPOSITION_DEPTH` = 8, overridable
     per call) is a second, independent cap: even a long ACYCLIC chain of
     distinct procedure versions stops expanding past this depth and
     raises `ProcedureCompositionDepthExceeded`, rather than trusting
     cycle detection alone to bound recursion.
A pinned reference to a (procedure_id, version) pair with no matching row
raises `UnresolvedSubprocedureRef` -- composition never falls back to
"whatever the latest version is now" (spec's explicit "version
ambiguity" prohibition).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import UUID

from app.execution.implementations import validate_implementation_hint
from app.models.plan import PlanNode, ProcedureRef

DEFAULT_MAX_COMPOSITION_DEPTH = 8


class ProcedureCompositionError(ValueError):
    """Base: a step's sub-procedure reference cannot be composed as given.
    Producer-side contract violation -- callers surface this verbatim,
    never swallow it into a partial/truncated graph."""


class ProcedureCompositionCycle(ProcedureCompositionError):
    """A (procedure_id, version) pair already appears in the current
    expansion chain -- composing it again would recurse forever."""


class ProcedureCompositionDepthExceeded(ProcedureCompositionError):
    """Independent safety net from cycle detection: expansion nested past
    `max_depth` distinct levels, cyclic or not."""


class UnresolvedSubprocedureRef(ProcedureCompositionError):
    """A step pins an exact (procedure_id, version) that does not exist.
    Never falls back to another version -- the spec's explicit
    "exact procedure version references must be pinned" prohibition."""


def _step_goal(step: dict) -> str:
    """Real corpora aren't uniform: rows written before steps carried a
    `goal` field use `action` instead (same legacy shape
    app/mcp_server/server.py::_render_step already defends against --
    found live, this pass, when this function first hit a real
    older-shaped procedure and raised a bare KeyError). Falls back to a
    stringified step rather than crashing on a shape this substrate has
    always tolerated elsewhere."""
    return step.get("goal") or step.get("action") or str(step)


def _step_ref(step: dict) -> Optional[ProcedureRef]:
    """Lift a step's `subprocedure_ref` (if present) into a real,
    validated ProcedureRef -- both halves required, same as the model's
    own documented contract; a versionless `subprocedure_ref` raises here,
    before any storage boundary is reached, rather than being silently
    ignored."""
    raw = step.get("subprocedure_ref")
    if raw is None:
        return None
    return ProcedureRef(**raw)


def _step_implementation_hint(step: dict) -> Optional[tuple[str, ...]]:
    """Lift a step's `implementation_hint` (if present) into a real,
    validated tuple -- a single kind string or a preference-ordered list
    are both accepted (see app.execution.implementations module
    docstring); a hint naming a kind outside the closed vocabulary raises
    here (ImplementationViolation), before any storage boundary is
    reached, rather than being silently accepted or silently dropped."""
    return validate_implementation_hint(step.get("implementation_hint"))


def steps_to_linear_nodes(steps: list[dict]) -> list[PlanNode]:
    """`steps`: a procedure's stored steps, each `{"order": int, "goal": str, ...}`
    (extra keys ignored, except `subprocedure_ref` and `implementation_hint`
    -- see module docstring). Returns PlanNodes in order, each depending
    on the PREVIOUS element in sorted sequence -- a straight chain,
    matching exactly what a linear procedure already is.

    REAL BUG this fixed, found live against a real stored procedure:
    deriving deps as `order - 1` assumes `order` is contiguous, 0-indexed,
    gapless -- a real corpus is not guaranteed to be (some older rows are
    1-indexed, e.g.). Dependency is really "whichever step sorts
    immediately before this one", which is a POSITION relationship, not
    an arithmetic one on the `order` value itself.

    A step carrying `subprocedure_ref` still gets one ordinary PlanNode
    here (with `step_ref` populated) -- this function never touches a
    pool and never expands. Expansion is `expand_procedure_steps()`,
    below, run by a caller BEFORE compile_plan.
    """
    ordered = sorted(steps, key=lambda s: s["order"])
    return [
        PlanNode(
            order=s["order"], goal=_step_goal(s), step_ref=_step_ref(s),
            implementation_hint=_step_implementation_hint(s),
            deps=[ordered[i - 1]["order"]] if i > 0 else [],
        )
        for i, s in enumerate(ordered)
    ]


# --------------------------------------------------------------- fetch


async def fetch_procedure_version(pool: Any, procedure_id: UUID, version: int) -> Optional[dict]:
    """Real fetch-by-exact-(procedure_id, version) lookup for pinned
    `subprocedure_ref` resolution.

    Deliberately NOT `app/services/procedures.py::get_procedure` -- that
    fetches by the immutable row `id`, not by the (procedure_id, version)
    pair a step actually pins. Migration 23's own
    `procedures_procedure_id_version_key` UNIQUE constraint guarantees at
    most one row can ever match this query, so there is no ambiguity to
    resolve and no "pick the latest" fallback to accidentally take."""
    row = await pool.fetchrow(
        "SELECT * FROM procedures WHERE procedure_id = $1 AND version = $2",
        procedure_id, version,
    )
    return dict(row) if row else None


FetchProcedureVersion = Callable[[UUID, int], Awaitable[Optional[Mapping[str, Any]]]]


# ------------------------------------------------------------- splicing


def _renumber_group(group: list[PlanNode], start: int) -> list[PlanNode]:
    """Give one already-self-contained linear group (either a single
    ordinary node, or the fully expanded subgraph spliced in for a
    `step_ref` node) fresh, globally-unique, contiguous orders starting
    at `start`. Internal deps (every member except the head -- a
    self-contained linear subgraph's head always has empty deps by
    construction, see `expand_composed_nodes`) are remapped alongside.
    The head's deps are intentionally left EMPTY here; the caller
    overwrites them afterward with the correctly-rewired external
    (cross-group) dependency, once every group's new order range is
    known."""
    local_map = {n.order: start + i for i, n in enumerate(group)}
    renumbered: list[PlanNode] = []
    for i, n in enumerate(group):
        new_deps = [] if i == 0 else [local_map[d] for d in n.deps]
        renumbered.append(n.model_copy(update={"order": local_map[n.order], "deps": new_deps}))
    return renumbered


async def expand_composed_nodes(
    fetch: FetchProcedureVersion,
    nodes: list[PlanNode],
    *,
    chain: tuple[tuple[UUID, int], ...] = (),
    depth: int = 0,
    max_depth: int = DEFAULT_MAX_COMPOSITION_DEPTH,
) -> list[PlanNode]:
    """The real recursive splice: expand every `step_ref` node in `nodes`
    into its referenced procedure's own (recursively expanded) subgraph,
    rewire dependency edges so downstream nodes depend on the spliced
    subgraph's terminal node instead of the reference node, and return one
    flat, contiguously-renumbered, still-linear node list -- indistinguishable
    at execution time from a hand-authored graph of the same shape.

    `fetch(procedure_id, version)` is caller-injected (matches this
    module's own `fetch_procedure_version`) so this function stays
    testable against a fake without a real pool.
    """
    if depth > max_depth:
        raise ProcedureCompositionDepthExceeded(
            f"V-COMPOSE: expansion nested past max_depth={max_depth} -- "
            "either genuinely too deep, or a cycle cycle-detection didn't catch "
            "(raise max_depth explicitly if this depth is legitimate)"
        )

    # Pass 1: expand each node independently into its own self-contained
    # linear group (a singleton for an ordinary node, a recursively
    # expanded subgraph for a step_ref node).
    groups: list[list[PlanNode]] = []
    for n in nodes:
        if n.step_ref is None:
            groups.append([n])
            continue
        key = (n.step_ref.procedure_id, n.step_ref.version)
        if key in chain:
            raise ProcedureCompositionCycle(
                f"V-COMPOSE: cycle detected -- procedure {key[0]} v{key[1]} is "
                f"already in the current expansion chain {chain}"
            )
        sub = await fetch(*key)
        if sub is None:
            raise UnresolvedSubprocedureRef(
                f"V-COMPOSE: pinned sub-procedure {key[0]} v{key[1]} does not "
                "exist -- refusing to silently resolve to another version"
            )
        sub_steps = sub.get("steps") or []
        sub_nodes = steps_to_linear_nodes(sub_steps)
        sub_expanded = await expand_composed_nodes(
            fetch, sub_nodes, chain=chain + (key,), depth=depth + 1, max_depth=max_depth,
        )
        groups.append(sub_expanded)

    # Pass 2: renumber every group to a fresh, globally-unique, contiguous
    # range, and record each original node's (new_head_order, new_tail_order)
    # -- the handle downstream original deps will be rewired through.
    renumbered_groups: list[list[PlanNode]] = []
    order_map: dict[int, tuple[int, int]] = {}
    counter = 0
    for orig, group in zip(nodes, groups):
        renumbered = _renumber_group(group, counter)
        counter += len(renumbered)
        order_map[orig.order] = (renumbered[0].order, renumbered[-1].order)
        renumbered_groups.append(renumbered)

    # Pass 3: rewire each group's head deps through order_map -- a node
    # that originally depended on parent-level order D now depends on the
    # TAIL of D's (possibly-expanded) group, never D's head, so downstream
    # scheduling correctly waits for the entire spliced subgraph to finish.
    flat: list[PlanNode] = []
    for orig, renumbered in zip(nodes, renumbered_groups):
        head_deps = [order_map[d][1] for d in orig.deps]
        renumbered[0] = renumbered[0].model_copy(update={"deps": head_deps})
        flat.extend(renumbered)
    return flat


async def expand_procedure_steps(
    pool: Any,
    *,
    procedure_id: UUID,
    procedure_version: int,
    steps: list[dict],
    max_depth: int = DEFAULT_MAX_COMPOSITION_DEPTH,
) -> list[PlanNode]:
    """Convenience entry point a caller runs BEFORE `compile_plan`: convert
    the ROOT procedure's own steps to linear nodes, then fully expand any
    `subprocedure_ref` steps against real storage via `pool`. Seeds the
    cycle-detection chain with the root's own (procedure_id, version) so a
    step that references the root procedure itself is refused as a cycle,
    not treated as a fresh expansion."""
    root_nodes = steps_to_linear_nodes(steps)

    async def _fetch(pid: UUID, version: int) -> Optional[dict]:
        return await fetch_procedure_version(pool, pid, version)

    return await expand_composed_nodes(
        _fetch, root_nodes,
        chain=((procedure_id, procedure_version),),
        depth=0, max_depth=max_depth,
    )
