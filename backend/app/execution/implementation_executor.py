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
  `bind_implementation`) are real, standalone, and fully tested.

  `bind_plan_implementations()` (below) is the follow-up: a separate
  async pass that runs AFTER `compile_plan()` and BEFORE
  `persist_compiled_plan()`, composing the two primitives above over
  every node of an already-compiled (not yet persisted) `CompiledPlan`.
  It is honest, standalone, real durable-id binding, composed two ways:

  (a) `PlanNode.task_node_id` -- a real, typed field (added alongside
      `procedure_graph.py::_step_task_node_id()`): a stored procedure
      step that names `{"task_node_id": "<uuid>"}` is threaded through
      `steps_to_linear_nodes()` verbatim, never fabricated from `goal`/
      `action` text. `bind_plan_implementations()` reads it directly off
      each node -- no schema change was needed, since `task_graphs.nodes`
      is already a JSONB column (`db/23_plan_persistence.sql`); adding a
      Pydantic field there is purely additive, same precedent as
      `implementation_hint` before it.
  (b) An explicit `task_node_ids` mapping (`{node.order: task_node_id}`),
      still accepted for callers who resolve a linkage OUTSIDE any single
      step (e.g. `server.py::_bind_plan_to_registry`'s real, procedure-
      level `procedures.migrated_from_task_node_id` link, applied
      uniformly across every node of the compiled graph) -- (a) never
      overrides an explicit mapping entry for the same node.

  Neither path is wired into `server.py`'s four real compile+persist call
  sites' STEP DATA today: most stored procedures still name only a goal
  string per step, so `task_node_id` is `None` for the overwhelming
  majority of real nodes -- matching today's real behavior exactly (see
  `execute_implementation`'s outcome 1: `implementation_id is None` falls
  back to the frontier provider, unchanged). The field exists so a step
  that DOES carry real linkage (written by a future extraction/synthesis
  pass, or a test constructing one directly) resolves automatically,
  without any caller needing an out-of-band map.
"""
from __future__ import annotations

from dataclasses import replace as _dataclasses_replace
from typing import Any, Mapping, Optional

import asyncpg

from app.execution import implementation_registry
from app.execution import providers
from app.execution.graph_executor import NodeResult
from app.execution.plans import CompiledPlan, _graph_content, _plan_content, canonical_json, sha256_hex
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
    satisfying, when known -- the caller (`bind_plan_implementations`)
    resolves it either from `node.task_node_id` (a real field the node
    itself may carry, lifted from a stored step's own `task_node_id`) or
    from an explicit out-of-band mapping for callers who know the linkage
    some other way (e.g. a procedure-level link, or a test constructing
    one directly). `None` means "no task node link is known for this
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


async def bind_plan_implementations(
    pool: asyncpg.Pool,
    compiled: CompiledPlan,
    *,
    scope: AccessScope,
    task_node_ids: Optional[Mapping[int, str]] = None,
) -> CompiledPlan:
    """
    The binding STAGE (directive Sec 20's "planning -> resolve -> bind ->
    persist -> execute" ordering, run for real): given an already-compiled,
    not-yet-persisted `CompiledPlan`, resolves and freezes a durable
    `implementation_id` onto every node it can, and hands back a NEW
    `CompiledPlan` -- never mutates `compiled` or anything inside it
    (this codebase's models are immutable-by-convention; see
    `bind_implementation`'s own docstring).

    Callers run this AFTER `compile_plan()` and BEFORE
    `persist_compiled_plan()` (module docstring explains why this is a
    separate pass rather than threaded into `compile_plan` itself).
    `compile_plan()` is never called from here, never imported for its
    behavior beyond the `CompiledPlan` shape it produces, and this
    function does not change anything about how it works -- proven by
    `tests/test_plan_implementation_binding_offline.py`, which asserts a
    `compile_plan()` output run back through this function with no
    resolvable task_node_ids comes back byte-identical (same object,
    since there is nothing to change -- see the early-return below).

    `task_node_ids`: an explicit `{node.order: task_node_id}` mapping --
    a plan node has no first-class field naming its real `task_nodes.id`
    today (see module docstring), so this function never guesses one; a
    node whose `order` has no entry (the default, `task_node_ids=None`,
    means EVERY node) simply resolves nothing for that node, which
    composes with `resolve_implementation_for_node`'s own honest `None`
    for "no task node link is known" and `bind_implementation`'s own
    honest "no id to freeze" no-op.

    Nodes that resolve nothing keep `implementation_id=None` exactly as
    `compile_plan()` left them -- today's real, unchanged fallback
    (`execute_implementation` still dispatches those to the frontier
    provider). When NOT ONE node in the graph resolves anything, this
    function returns `compiled` itself, unchanged -- there is nothing to
    freeze, so there is nothing to re-hash either.

    When at least one node DOES resolve a durable implementation, the
    resulting graph's node content is real, new content the original
    `compile_plan()` call could not have hashed (it had no pool, so it
    never looked up the registry) -- `graph_hash` and the plan's
    `content_hash` are recomputed over the bound nodes using the exact
    same recipe `compile_plan()` itself uses (`plans.py`'s own
    `_graph_content`/`_plan_content`/`canonical_json`/`sha256_hex`,
    imported rather than reimplemented, so the two can never drift
    apart). This is a deliberate, honest consequence: two compiles of the
    identical procedure/task, run against a registry that has since
    changed (a new implementation activated in between), now bind
    differently and are correctly DIFFERENT plan content -- the dedup
    contract (`find_rebindable_plan`) still holds, it just now also
    accounts for the durable identity actually bound, which is real
    content of what will execute, not merely a resolution detail.
    """
    task_node_ids = task_node_ids or {}
    bound_nodes: list[PlanNode] = []
    any_bound = False
    for node in compiled.graph.nodes:
        task_node_id = task_node_ids.get(node.order) or node.task_node_id
        resolved = await resolve_implementation_for_node(pool, node, task_node_id, scope=scope)
        bound = bind_implementation(node, resolved)
        if bound.implementation_id is not None:
            any_bound = True
        bound_nodes.append(bound)

    if not any_bound:
        return compiled

    new_graph_hash = sha256_hex(canonical_json(_graph_content(bound_nodes)))
    new_content_hash = sha256_hex(canonical_json(_plan_content(
        procedure=compiled.plan.procedure,
        procedure_content_hash=compiled.plan.procedure_content_hash,
        scope_type=compiled.plan.scope_type,
        scope_entity_id=compiled.plan.scope_entity_id,
        task_description=compiled.plan.task_description,
        parameters=compiled.plan.parameters,
        starting_state_id=compiled.plan.starting_state_id,
        resolved_claims=compiled.plan.resolved_claims,
        selected_branches=compiled.plan.selected_branches,
        implementations=compiled.plan.implementations,
        safety_check=compiled.plan.safety_check,
        verification_plan=compiled.plan.verification_plan,
        graph_hash=new_graph_hash,
        extractor_version=compiled.plan.extractor_version,
    )))

    new_graph = compiled.graph.model_copy(update={"nodes": bound_nodes, "graph_hash": new_graph_hash})
    new_plan = compiled.plan.model_copy(update={"content_hash": new_content_hash})
    return _dataclasses_replace(compiled, plan=new_plan, graph=new_graph)


def plan_implementation_id(compiled: CompiledPlan) -> Optional[str]:
    """The single durable implementation identity to record on this plan's
    `Execution` row, if the whole graph agrees on one. `executions` carries
    one `implementation_id` column per run (db/23_plan_persistence.sql),
    not one per node -- honest for today's real graphs (tier-2/
    reproduce_procedure compile overwhelmingly single-node, per
    `graph_executor.py`'s own docstring). When every node's bound
    `implementation_id` (already frozen by `bind_plan_implementations`, or
    left `None` when nothing resolved) agrees, that shared value is
    returned; a graph whose nodes disagree (some bound, some not, or bound
    to different durable ids) returns `None` rather than picking one node's
    identity arbitrarily -- an honest "no single identity fits this run"
    over a fabricated pick."""
    ids = {n.implementation_id for n in compiled.graph.nodes}
    if len(ids) == 1:
        return next(iter(ids))
    return None


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

    # B38: "missing dependencies/configuration produce typed terminal
    # errors" (AUTH_REQUIRED-class) -- `validate_invocation` already
    # exists (real, tested) but had ZERO production callers before this
    # check: a missing credential/network requirement was never
    # PRE-EMPTIVELY detected, it just failed later at the real
    # invocation attempt with whatever opaque error that produced. That
    # earlier behavior was never a SYNTHETIC success (a real failure
    # still occurred), but it also never gave a caller (or a retry/
    # routing decision) a typed, actionable reason to distinguish "this
    # would need a credential nobody has" from "this genuinely broke".
    # Checked here, before dispatch, so it applies uniformly to every
    # kind (adapter or provider), not duplicated in each one.
    problems = validate_invocation(implementation, context)
    if problems:
        return NodeResult(
            status="failure",
            data={"error_class": "auth_required", "missing_requirements": problems},
            notes=(
                f"step {node.order} ({node.goal}): implementation "
                f"{implementation['id']!r} ({implementation['name']!r}) requires "
                f"context this caller does not provide -- refusing rather than "
                f"attempting an invocation known in advance to fail: {'; '.join(problems)}"
            ),
        )

    kind = implementation["kind"]

    # B25/B27/B28: the real Adapter Resolver -- for kinds needing
    # PER-IMPLEMENTATION resolution (different 'api'/'tool' rows point
    # at different real endpoints, so there is no single process-wide
    # singleton the way frontier/deterministic have), `build_adapter`
    # constructs a fresh `Adapter` and this module injects the resolved
    # implementation row into `context["implementation"]` so `Adapter.
    # execute()`'s own resolve()/validate()/prepare()/invoke()/
    # collect_*()/cleanup() lifecycle (app/execution/adapters.py) can see
    # it. Checked BEFORE the pre-existing `providers.PROVIDER_REGISTRY`
    # singleton lookup so 'deterministic' now genuinely dispatches
    # through the literal B28 8-step lifecycle (LocalAdapter) rather than
    # the older DeterministicProvider shortcut -- same underlying
    # SubprocessSandboxExecutor, not a second sandbox mechanism.
    from app.execution.adapters import build_adapter

    adapter = build_adapter(kind)
    if adapter is not None:
        return await adapter.execute(node, {**context, "implementation": implementation})

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
