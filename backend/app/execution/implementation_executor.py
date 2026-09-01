"""
Resolve -> bind -> execute wiring between the durable
`implementation_registry.py` (a row per concrete implementation) and the
real execution mechanism in `providers.py` (one `ImplementationProvider`
class per KIND). Directive Sec 20-23.

WHERE THIS SITS relative to what already exists (read
`implementation_registry.py`, `providers.py`, and `implementations.py` in
full before touching this file -- none of the three is modified here):

  - `implementation_registry.py::resolve()` answers "which concrete,
    durable implementation should satisfy this task node" -- a thin,
    honest lookup over `implementation_tasks`, never a fabrication.
  - `providers.py::get_provider(kind)` answers "which real executor class
    realizes this KIND, if any" -- keyed by KIND, not by durable identity.
  - THIS module is the missing bridge directive Sec 20's "planning ->
    resolve -> bind -> persist -> execute frozen plan" ordering asks for:
    look up a durable identity for a node (`resolve_implementation_for_
    node`), freeze it onto the node (`bind_implementation`), and later
    execute a node whose identity is ALREADY frozen by looking up its kind
    and dispatching to the matching provider (`execute_implementation`).

NOT WIRED INTO `plans.py::compile_plan`'s HOT PATH, AND WHY (be honest,
not evasive -- same "build the real primitive, be honest about what's not
wired in yet" posture `implementation_registry_audit.md` and `providers.py`
both already establish for this exact seam):

  `compile_plan()` is deliberately pure and pool-free (its own docstring:
  "this module stays pure/pool-free so its contracts are provable
  offline"; `plan_persistence.py`'s docstring says the same about the
  storage split). `resolve_implementation_for_node()` needs a real
  `asyncpg.Pool` (it calls `implementation_registry.resolve()`, a real
  query) -- threading a pool into `compile_plan()` would break that
  offline-provable contract for every existing caller and test, for a
  registry that, per the audit, almost nothing is registered against yet.
  The two primitives here (`resolve_implementation_for_node` +
  `bind_implementation`) are real, standalone, and fully tested; wiring
  them into `compile_plan`'s own call sites (there is exactly one real
  compile pipeline caller today, `find_best_way`'s tier-2 path) is a
  follow-up integration change for whichever lane owns that call site,
  not a change to `compile_plan` itself.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.execution import implementation_registry
from app.execution import providers
from app.execution.graph_executor import NodeResult
from app.models.plan import PlanNode
from app.services.access import AccessScope


async def resolve_implementation_for_node(
    pool: asyncpg.Pool,
    node: PlanNode,
    task_node_id: Optional[str],
    *,
    scope: AccessScope,
) -> Optional[dict]:
    """
    "Which durable implementation, if any, should satisfy this node's
    work?" Composes `implementation_registry.resolve()` with the node's
    own `implementation_hint` (a KIND preference tuple) -- never invents a
    preference the node didn't already carry.

    `task_node_id` is the real `task_nodes.id` this plan node is
    satisfying, when known -- a plan node has no first-class field
    naming one today (see module docstring: nothing wires this into
    `compile_plan` yet), so callers that DO know it (e.g. a future
    compile-time integration, or a test constructing one directly) pass
    it explicitly. `None` means "no task node link is known for this
    node" -- honestly returns `None` rather than guessing one, since
    `implementation_registry.resolve()` requires a real task_node_id to
    query against.

    Returns the resolved implementation row (a plain dict, per
    `implementation_registry._row_to_dict`'s shape), or `None` when
    nothing durable is registered and active for this task -- the
    overwhelmingly common case today (per the registry audit, almost
    nothing is registered yet). Never raises for "nothing found", and
    never fabricates a fallback row -- `execute_implementation` below is
    what owns the honest frontier-default fallback, not this function.
    """
    if not task_node_id:
        return None
    return await implementation_registry.resolve(
        pool, task_node_id, scope=scope, hint_kinds=node.implementation_hint,
    )


def bind_implementation(node: PlanNode, implementation: Optional[dict]) -> PlanNode:
    """
    The FREEZE step (directive Sec 20-21): returns a NEW `PlanNode` (this
    codebase's models are immutable-by-convention -- `.model_copy(update=
    ...)` is the idiom every other mutator in this codebase uses over a
    pydantic model, e.g. `plans.py`'s own node-normalization helpers) with
    `implementation_id` set to the resolved implementation's real,
    durable `id` (as a string).

    Once bound, this identity does not get re-resolved: calling this
    function again with a DIFFERENT `implementation` argument still
    produces a node carrying whatever id was passed THIS call -- freezing
    happens by the caller choosing not to call this function again on an
    already-bound node, not by any internal guard here (this function is
    a pure, stateless copy; nothing about the object itself refuses a
    second call). The real "does not silently re-resolve" guarantee
    lives one level up: a caller that has already persisted or is already
    executing a bound node simply never calls `resolve_implementation_for_
    node` again for it (proven in this module's own tests via replay:
    resolving again for a newer implementation version never mutates a
    node that already carries an older id -- see
    test_implementation_executor_e2e.py's "implementation change/replay"
    scenario).

    `implementation=None` leaves `implementation_id=None` -- today's real
    fallback: `resolve_implementation()` in `implementations.py` (and, at
    execution time, `execute_implementation` below) already handle "no
    durable implementation found" by defaulting to "frontier" kind
    dispatch, exactly as they do today. This function never fabricates an
    id to fill that gap.
    """
    new_id = str(implementation["id"]) if implementation is not None else None
    return node.model_copy(update={"implementation_id": new_id})


async def execute_implementation(
    pool: asyncpg.Pool,
    node: PlanNode,
    context: dict,
    *,
    scope: AccessScope,
) -> NodeResult:
    """
    Execute a node whose `implementation_id` is ALREADY bound (via
    `bind_implementation`, or a real persisted plan carrying one).

    Three real outcomes, never a fourth silently-fabricated one:

      1. `node.implementation_id is None` -- no durable identity was ever
         bound (today's overwhelmingly common case). Falls back to the
         EXACT existing default behavior: `providers.get_provider(
         "frontier").execute(node, context)` -- behaves identically to how
         the system runs today when nothing about this wave's registry is
         used at all (directive Sec 88's explicit backward-compatibility
         requirement: "existing no-hint procedures must continue to
         execute... do not regress").
      2. `node.implementation_id` names a real, visible row whose `kind`
         has a registered provider (`providers.PROVIDER_REGISTRY`) --
         dispatches to that provider's real `execute()`.
      3. `node.implementation_id` names a real row but its `kind` has NO
         registered provider (e.g. 'tool'/'slm'/'human'/'wasm'/
         'computer_use'/'api' -- all real, registrable kinds with no real
         Python-side executor yet, per `providers.py`'s own docstring),
         OR the id does not resolve to any visible row at all (deleted,
         wrong scope, or a stale/bad id) -- returns an honest
         `NodeResult(status="failure", ...)` naming exactly which case it
         is. Never silently substitutes `FrontierProvider`, never raises.
    """
    if node.implementation_id is None:
        frontier = providers.get_provider("frontier")
        assert frontier is not None, "frontier provider must always be registered"
        return await frontier.execute(node, context)

    implementation = await implementation_registry.get(pool, node.implementation_id, scope=scope)
    if implementation is None:
        return NodeResult(
            status="failure",
            notes=(
                f"step {node.order} ({node.goal}): bound implementation_id "
                f"{node.implementation_id!r} does not resolve to any visible "
                "implementation row -- never silently falling back to frontier "
                "for a bound-but-unresolvable identity"
            ),
        )

    kind = implementation["kind"]
    provider = providers.get_provider(kind)
    if provider is None:
        return NodeResult(
            status="failure",
            notes=(
                f"step {node.order} ({node.goal}): implementation "
                f"{implementation['id']!r} ({implementation['name']!r}) has kind "
                f"{kind!r}, which has no registered execution provider yet "
                f"(providers.py PROVIDER_REGISTRY covers: "
                f"{sorted(providers.PROVIDER_REGISTRY)}) -- refusing rather than "
                "silently substituting a different kind's provider"
            ),
        )
    return await provider.execute(node, context)


def check_requirements(implementation: dict, available: dict) -> bool:
    """
    Real, simple boolean: does `available` (a dict describing what is
    ACTUALLY present in this caller's environment -- e.g.
    `{"network": True, "credentials": ["graphify"]}`) satisfy every
    truthy key of `implementation['requirements']`.

    Deliberately a presence check, not a deep semantic validator: a
    requirement value that is truthy (bool True, a non-empty list/str/
    dict) means "this capability is needed"; satisfaction means
    `available` carries a truthy value under the SAME key. This does not
    confirm, e.g., that `available['credentials']` names the SPECIFIC
    credential a `requirements['credentials']` list names -- that finer
    check is what `validate_invocation` performs for the one requirement
    shape (a `credentials` list) this module can meaningfully compare
    element-by-element. An honest boolean, not a fabricated pass.
    """
    requirements = implementation.get("requirements") or {}
    for key, value in requirements.items():
        if not value:
            continue
        if not available.get(key):
            return False
    return True


def validate_invocation(implementation: dict, context: dict) -> list[str]:
    """
    Human-readable problems checking whether `context` plausibly
    satisfies `implementation['requirements']`. Empty list = valid.

    Deliberately narrow, per this task's own instruction not to
    over-engineer: only two requirement shapes are actually checked here,
    because they are the only ones this module can check without
    guessing at a protocol-specific meaning it doesn't have:

      - `requirements['network']` (bool) -- if truthy, `context` must
        carry a truthy `network_access` key.
      - `requirements['credentials']` (list[str]) -- if given, every
        named credential must appear in `context['credentials']` (also a
        list); missing ones are named explicitly.

    Every OTHER requirement key (`resource_requirements`-shaped things
    like CPU/memory, `auth_requirements`'s `credential_ref` shape,
    protocol-specific `locator`/`invocation` fields, ...) is NOT checked
    here -- naming that limitation is the honest behavior this function
    commits to, not silently declaring them satisfied.
    """
    problems: list[str] = []
    requirements = implementation.get("requirements") or {}

    if requirements.get("network") and not context.get("network_access"):
        problems.append(
            "requirements['network'] is true but context does not indicate "
            "network access is available (context.get('network_access') is falsy)"
        )

    credentials_required = requirements.get("credentials")
    if credentials_required:
        if isinstance(credentials_required, (list, tuple)):
            available_credentials = set(context.get("credentials") or [])
            missing = [c for c in credentials_required if c not in available_credentials]
            if missing:
                problems.append(
                    f"requirements['credentials'] names {list(credentials_required)}, "
                    f"context is missing {missing}"
                )
        elif not context.get("credentials"):
            problems.append(
                "requirements['credentials'] is set but context carries no credentials"
            )

    return problems
