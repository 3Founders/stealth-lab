"""
Procedure Graph API (directive §41) + Solution read-composition (§32.5).

Pure read composition, no new schema, no migration -- mirrors the house
style `app/api/graph.py` and `app/services/claim_traversal.py` already
established: reuse the real writers/readers that exist, apply
visibility/tenancy at the query boundary, and never fabricate a field a
caller asks for that has no real backing column.

Everything here composes over primitives that already exist and are
already tested elsewhere -- this module invents no new storage shape:

  - `app.services.procedures.get_procedure` -- the procedure version row.
    `procedures` carries NO `tenant_id` column (confirmed by reading
    `db/18_procedures.sql` in full and grepping every migration for an
    `ADD COLUMN ... tenant_id ... procedures` -- there is none, unlike
    `knowledge_nodes`/`evidence`/`executions`, which migration 29 gave
    one). Every query against `procedures` in this module therefore
    applies ONLY `visibility_predicate()`, never `scope_predicates()` --
    the same asymmetry `applicability.py`'s own candidate-pool query
    already lives with.
  - `app.execution.procedure_graph.expand_procedure_steps` -- the real,
    cycle-safe, depth-capped composition walk. Reused verbatim for
    `get_procedure_graph`; this module does not reimplement graph
    traversal.
  - `app.execution.implementations.resolve_implementation` -- the real
    (kind -> executor) registry. Reused verbatim for the Solution view's
    `implementation` field; never a second guess at what's executable.
  - `app.services.procedure_extraction.capability.wilson_interval` /
    `band_for_p` / `route_for_p` -- the real, pure P-estimation math.
    Reused for the Solution view's capability estimate WITHOUT routing
    it through `compute_capability()`'s `OutcomeRecord`/`CapabilityScope`
    machinery, because that machinery requires a non-blank `environment`
    per outcome and this codebase has no real "which environment did
    this run in" column on `evidence` -- inventing one to satisfy the
    model would be exactly the fabrication CLAUDE.md forbids. The P
    estimate, its Wilson interval, and P-based routing are honest
    (Wilson math + evidence counts only); the environment-gated L4/L5
    tiers are NOT computed here (see `_capability_estimate` docstring).

Claims referenced by a procedure's preconditions are read directly off
`knowledge_nodes` via each precondition's own `claim_id` (the real,
author-time provenance pointer `applicability.py::_claim_matches_
precondition` already narrows against) -- not `project_state`'s
subject-match projection (`claim_traversal.explain()`'s own mechanism),
which is a live/current-belief lookup, not "which claims does this
procedure's authored precondition literally point at."
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.execution.implementations import (
    ImplementationResolution,
    resolve_implementation,
    validate_implementation_hint,
)
from app.execution.procedure_graph import (
    ProcedureCompositionError,
    expand_procedure_steps,
)
from app.services.access import AccessScope, TenantScope, scope_predicates, visibility_predicate
from app.services.applicability import PROCEDURE_COLS_NO_HEAVY
from app.services.procedure_extraction.capability import (
    band_for_p,
    route_for_p,
    wilson_interval,
)
from app.services.procedures import get_procedure
from app.services.retrieval_document import (
    build_applicability_summary,
    build_failure_modes,
)

# Evidence rows this module treats as outcome-bearing for the P estimate.
# `procedure_evidence_stats` (db/24_evidence.sql, as amended by
# db/34_evidence_stats_count_failures.sql) counts an attempt for every
# live row of these two types with a terminal `outcome_status`, in EITHER
# direction -- a recorded failure (direction='contradicts' by
# outcome_to_evidence's default) is an attempt that lowers P, not a row
# that vanishes. Restated here (not queried from the view) because the
# view aggregates per (procedure_id, version) and this module needs the
# individual rows too (for independence_group counting and the raw
# evidence listing endpoint).
_OUTCOME_BEARING_EVIDENCE_TYPES = ("execution_result", "reproduction")


# ---------------------------------------------------------------------------
# Shared: visibility-checked procedure fetch. `procedures` has no
# tenant_id column (see module docstring) -- only visibility applies.
# ---------------------------------------------------------------------------


async def _fetch_visible_procedure(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> Optional[dict]:
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    # Explicit projection, not SELECT * -- the ~15 KB/row `embedding` vector
    # and multi-KB `retrieval_document` are never read on any detail path and
    # were the bulk of this endpoint's network egress.
    row = await pool.fetchrow(
        f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures "
        f"WHERE id = $1::uuid AND {vis_sql}",
        procedure_row_id, *vis_params,
    )
    return dict(row) if row else None


def _precondition_claim_ids(procedure: dict) -> list[str]:
    """Every distinct, non-blank `claim_id` a procedure's preconditions
    carry, order-preserving. `preconditions` predates this field on many
    real rows (`procedures.py::capture_procedure`'s own docstring: "Passing
    preconditions=[] here is honest about what capture alone can produce
    without that wiring existing yet") -- a precondition entry without a
    `claim_id` contributes nothing, which is an honest empty, not an
    error."""
    seen: set[str] = set()
    ids: list[str] = []
    for precondition in (procedure.get("preconditions") or []):
        claim_id = precondition.get("claim_id")
        if claim_id and str(claim_id) not in seen:
            seen.add(str(claim_id))
            ids.append(str(claim_id))
    return ids


# ---------------------------------------------------------------------------
# get_procedure_claims
# ---------------------------------------------------------------------------


async def get_procedure_claims(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> list[dict]:
    """Claims referenced by this procedure's preconditions, via each
    precondition's own `claim_id` pointer -- real author-time provenance,
    not a live subject re-lookup (see module docstring). An invisible or
    missing procedure, or one whose preconditions carry no `claim_id` at
    all, both return an honest `[]`."""
    procedure = await _fetch_visible_procedure(pool, procedure_row_id, scope=scope)
    if procedure is None:
        return []

    claim_ids = _precondition_claim_ids(procedure)
    if not claim_ids:
        return []

    scope_sql, scope_params, _ = scope_predicates(
        scope, TenantScope.unrestricted(), param_index=2,
    )
    rows = await pool.fetch(
        f"""
        SELECT * FROM knowledge_nodes
        WHERE id = ANY($1::uuid[]) AND node_type = 'claim'
          AND t_invalid IS NULL AND {scope_sql}
        """,
        [UUID(cid) for cid in claim_ids], *scope_params,
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# get_procedure_evidence
# ---------------------------------------------------------------------------


async def get_procedure_evidence(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> list[dict]:
    """Real evidence rows targeting this exact procedure VERSION row
    (`target_type='procedure', target_id=procedure_row_id`) -- mirrors
    `claim_evidence.get_claim_evidence`'s SELECT shape exactly, filtered
    by `target_id` alone (the row id is already version-specific; a
    procedure row's `id` never gets reused across versions --
    `supersede_procedure` always mints a fresh `uuid7()`). An invisible or
    missing procedure returns an honest `[]`."""
    procedure = await _fetch_visible_procedure(pool, procedure_row_id, scope=scope)
    if procedure is None:
        return []

    scope_sql, scope_params, _ = scope_predicates(
        scope, TenantScope.unrestricted(), param_index=2,
    )
    rows = await pool.fetch(
        f"""
        SELECT * FROM evidence
        WHERE target_type = 'procedure' AND target_id = $1::uuid
          AND t_invalid IS NULL AND {scope_sql}
        ORDER BY t_valid ASC
        """,
        procedure_row_id, *scope_params,
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# get_procedure_versions
# ---------------------------------------------------------------------------


async def get_procedure_versions(
    pool: asyncpg.Pool, procedure_id: str, *, scope: AccessScope,
) -> list[dict]:
    """Every version row sharing `procedure_id` (the stable cross-version
    handle -- `procedures.py`'s own docstring, `idx_procedures_procedure_id`),
    oldest first. Reuses the real query shape `procedure_graph.py::
    fetch_procedure_version` already establishes for a SINGLE (procedure_id,
    version) lookup, widened to the whole chain -- `procedures.py` has no
    dedicated "walk the version chain" function to call (only
    `supersede_procedure`, which CREATES the next link, not lists them),
    so this is the direct, honest SELECT rather than a guessed-at helper.
    Includes invalidated (superseded) rows -- a "versions" listing that
    silently hid history would defeat the point of asking for it; each
    row's own `t_valid`/`t_invalid` says whether it is the live head."""
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures
        WHERE procedure_id = $1::uuid AND {vis_sql}
        ORDER BY version ASC
        """,
        procedure_id, *vis_params,
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# get_procedure_graph
# ---------------------------------------------------------------------------


async def get_procedure_graph(
    pool: asyncpg.Pool, procedure_row_id: str, *, depth: int = 2, scope: AccessScope,
) -> Optional[dict]:
    """The procedure's own task/step graph, composed/expanded via the
    real `expand_procedure_steps()` (cycle detection + depth cap already
    live there -- this function does not re-derive either). `depth` is
    threaded straight through as `max_depth`. Returns `None` for a
    missing/invisible procedure; raises `ProcedureCompositionError` (its
    real subclasses -- cycle/depth-exceeded/unresolved-ref) verbatim for
    a genuinely malformed composition, same as the underlying module --
    a router-level caller decides how to surface that, this function does
    not swallow it into a partial graph."""
    procedure = await _fetch_visible_procedure(pool, procedure_row_id, scope=scope)
    if procedure is None:
        return None

    nodes = await expand_procedure_steps(
        pool,
        procedure_id=UUID(str(procedure["procedure_id"])),
        procedure_version=procedure["version"],
        steps=procedure.get("steps") or [],
        max_depth=depth,
    )
    return {
        "procedure_row_id": str(procedure_row_id),
        "procedure_id": str(procedure["procedure_id"]),
        "version": procedure["version"],
        "depth": depth,
        "nodes": [node.model_dump(mode="json") for node in nodes],
    }


# ---------------------------------------------------------------------------
# get_procedure_detail
# ---------------------------------------------------------------------------


async def get_procedure_detail(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> Optional[dict]:
    """Composed procedure detail: the procedure row itself + its
    precondition-referenced claims + an evidence summary + the
    implementation kind(s) its steps advertise. Honest `None` for a
    missing or invisible row -- never a placeholder object."""
    procedure = await _fetch_visible_procedure(pool, procedure_row_id, scope=scope)
    if procedure is None:
        return None

    claims = await get_procedure_claims(pool, procedure_row_id, scope=scope)
    evidence = await get_procedure_evidence(pool, procedure_row_id, scope=scope)

    success_count = sum(1 for e in evidence if e.get("outcome_status") == "success")
    failure_count = sum(1 for e in evidence if e.get("outcome_status") == "failure")

    implementation_kinds = _advertised_implementation_kinds(procedure)

    return {
        "id": str(procedure["id"]),
        "procedure_id": str(procedure["procedure_id"]),
        "version": procedure["version"],
        "family_id": str(procedure["family_id"]) if procedure.get("family_id") else None,
        "name": procedure["name"],
        "goal": procedure["goal"],
        # Human-facing (plan Part 14): the detail page leads with these,
        # `name` stays the technical secondary label.
        "display_name": procedure.get("display_name") or procedure["name"],
        "display_description": procedure.get("display_description") or procedure["goal"],
        "applicability_summary": build_applicability_summary(dict(procedure)),
        "failure_modes": build_failure_modes(dict(procedure)),
        "steps": procedure.get("steps") or [],
        "preconditions": procedure.get("preconditions") or [],
        "invariants": procedure.get("invariants") or [],
        "verification_state": procedure["verification_state"],
        "staleness": procedure["staleness"],
        "availability": procedure["availability"],
        "approval_status": procedure.get("approval_status"),
        "scope_type": procedure.get("scope_type"),
        "scope_entity_id": procedure.get("scope_entity_id"),
        "provenance": procedure.get("provenance"),
        "domain": procedure.get("domain"),
        "created_by": procedure.get("created_by"),
        "owner_id": procedure.get("owner_id"),
        "visibility": procedure.get("visibility"),
        "t_valid": procedure.get("t_valid"),
        "t_invalid": procedure.get("t_invalid"),
        "t_created": procedure.get("t_created"),
        "claims": claims,
        "evidence_summary": {
            "total": len(evidence),
            "success_count": success_count,
            "failure_count": failure_count,
        },
        "implementation_kinds": implementation_kinds,
    }


def _advertised_implementation_kinds(procedure: dict) -> list[str]:
    """Distinct implementation kinds this procedure's own steps advertise
    via `implementation_hint`, in first-seen order. A procedure with no
    hinted steps returns `[]` -- honest, not defaulted to `["frontier"]`;
    `resolve_implementation(None)`'s own DEFAULT_KIND fallback is a
    per-node runtime behavior, not a fact about what this procedure
    itself declared."""
    seen: list[str] = []
    for step in (procedure.get("steps") or []):
        hint = validate_implementation_hint(step.get("implementation_hint"))
        if hint is None:
            continue
        for kind in hint:
            if kind not in seen:
                seen.append(kind)
    return seen


# ---------------------------------------------------------------------------
# get_solution_view (directive §32.5)
# ---------------------------------------------------------------------------


def _capability_estimate(evidence: list[dict]) -> dict:
    """P-estimate + Wilson interval + P-based routing, computed directly
    from real evidence rows -- NOT routed through
    `procedure_extraction.capability.compute_capability()`, because that
    function's `OutcomeRecord` requires a non-blank `environment` per
    outcome and NOTHING in this codebase records which environment an
    `evidence` row's outcome ran in (confirmed by reading `db/24_
    evidence.sql` in full: no such column exists). Inventing one would be
    exactly the fabrication CLAUDE.md forbids, so the environment-gated
    L4 ("generalized")/L5 ("trusted") tiers and the L3 ("validated")
    verification-plan gate are NOT computed here -- `level_gated` is
    `None`, named and commented, not silently omitted.

    What IS honest and computed: `p_estimate`/`p_lower`/`p_upper` (Wilson
    interval over real success/total counts), `evidence_count`/
    `success_count` (real row counts), `independent_groups` (real
    DISTINCT `evidence.independence_group` count -- the one L2 gate input
    this composition DOES have real data for), `band` (the pure P ladder,
    ungated -- `capability.py::band_for_p`), and `routing` (pure P
    threshold, `capability.py::route_for_p`) -- both reused verbatim, no
    reimplementation of the D1-ratified thresholds.

    The stream is gated by `evidence_type` and `outcome_status` ONLY --
    NOT by `direction`. `outcome_to_evidence()` defaults a failure's
    `direction` to `'contradicts'`, so an `AND direction = 'supports'`
    filter here silently dropped every real recorded failure from the P
    estimate -- a procedure could accrue failures and its P would not
    move. A terminal `outcome_status IN ('success', 'failure')` on an
    outcome-bearing `evidence_type` already restricts to real recorded
    outcomes regardless of the supports/contradicts arrow; the failure
    count is exactly what pulls the Wilson lower bound down. (The
    `direction = 'supports'` filter is still correct where the question
    is "how much INDEPENDENT SUPPORTING evidence exists" -- see
    `procedure_evidence_stats.independent_supporting_required`,
    db/34_evidence_stats_count_failures.sql -- but that is a different
    question from "what is P".)
    """
    outcome_bearing = [
        e for e in evidence
        if e.get("evidence_type") in _OUTCOME_BEARING_EVIDENCE_TYPES
        and e.get("outcome_status") in ("success", "failure")
    ]
    total = len(outcome_bearing)
    successes = sum(1 for e in outcome_bearing if e["outcome_status"] == "success")

    p_lower, p_upper = wilson_interval(successes, total)
    p_estimate = p_lower

    independent_groups = len({
        e["independence_group"] for e in outcome_bearing if e.get("independence_group")
    })

    band = 0
    if total > 0 and successes > 0:
        band = band_for_p(p_estimate)

    return {
        "p_estimate": p_estimate,
        "p_lower": p_lower,
        "p_upper": p_upper,
        "evidence_count": total,
        "success_count": successes,
        "independent_groups": independent_groups,
        "band": band,
        "routing": route_for_p(p_estimate).value,
        # Named, not silently dropped: no real "environment"/"completed
        # review"/"verification plan satisfied" column exists on evidence
        # or procedures today, so the fully-gated L0-5 ladder
        # (compute_capability()'s L3/L4/L5 gates) cannot be honestly
        # computed from this composition alone.
        "level_gated": None,
    }


def _cost_estimate(procedure: dict) -> dict:
    """Real fields only, from `verification_stats` (the ticket-13 JSONB
    counter blob -- see `procedures.py::compute_utility`'s identical
    reads). `average_match_cost` is `match_cost_total / attempts`; `None`
    when there have been no attempts, never `0.0` (a never-run procedure
    has an UNDEFINED cost, not a zero one -- same reasoning
    `compute_utility()`'s own docstring gives for utility)."""
    stats = procedure.get("verification_stats") or {}
    attempts = stats.get("attempts", 0)
    average_match_cost = (
        stats["match_cost_total"] / attempts if attempts else None
    )
    return {
        "match_cost_total": stats.get("match_cost_total"),
        "average_match_cost": average_match_cost,
        "realised_savings_total": stats.get("realised_savings_total"),
        "mean_steps": stats.get("mean_steps"),
        # No real per-execution latency column exists anywhere on the
        # procedure/evidence path today (task_nodes.latency_estimate_ms is
        # a DIFFERENT object -- a task-node capability advertisement, not
        # this procedure's own recorded runtime) -- never fabricated.
        "latency_ms": None,
    }


async def get_solution_view(
    pool: asyncpg.Pool,
    procedure_row_id: str,
    *,
    implementation_id: Optional[str] = None,
    scope: AccessScope,
) -> Optional[dict]:
    """Directive §32.5: a "Solution" is modeled as a read composition over
    existing `procedures` + `execution/implementations.py` + `evidence`
    rows -- there is no `solutions` table, and this function creates none.
    In this v1, one Solution == one procedure+implementation pairing,
    ADDRESSED BY THE PROCEDURE'S OWN ROW ID (`procedure_row_id`) -- there
    being no independent Solution identity to address it by. Honest
    `None` for a missing/invisible procedure.

    `implementation_id` names a specific `executions.implementation_id`
    to look up runtime/provider detail for. That column is real
    (`db/23_plan_persistence.sql`) but is a "forward-compatible handle"
    with NO real writer anywhere in this codebase today (confirmed by
    grep) -- so a lookup against it honestly returns no rows right now;
    the field stays in the response, `None`, rather than silently
    omitted, so a future writer's data appears here with no API change.

    Every field below traces to a real column or a real, already-tested
    computation (see `_capability_estimate`/`_cost_estimate`/
    `_advertised_implementation_kinds` for exactly which). Fields the
    directive's own prose mentions that have NO real source anywhere in
    this schema (e.g. "license") are omitted from the payload entirely --
    commented here, not silently absent: **no `license` field exists
    because no table in this schema records one.**
    """
    procedure = await _fetch_visible_procedure(pool, procedure_row_id, scope=scope)
    if procedure is None:
        return None

    claims = await get_procedure_claims(pool, procedure_row_id, scope=scope)
    evidence = await get_procedure_evidence(pool, procedure_row_id, scope=scope)

    implementation_kinds = _advertised_implementation_kinds(procedure)
    implementation_resolutions: dict[str, dict] = {}
    for kind in implementation_kinds:
        resolution: ImplementationResolution = resolve_implementation((kind,))
        implementation_resolutions[kind] = {
            "kind": resolution.kind,
            "supported": resolution.supported,
            "strategy": resolution.strategy,
            "reason": resolution.reason,
        }
    if not implementation_kinds:
        # No step advertised a preference -- resolve_implementation(None)'s
        # own real DEFAULT_KIND fallback ("frontier"), reported honestly as
        # what WOULD run today, not asserted as something the procedure
        # itself declared (see _advertised_implementation_kinds docstring).
        default_resolution = resolve_implementation(None)
        implementation_resolutions[default_resolution.kind] = {
            "kind": default_resolution.kind,
            "supported": default_resolution.supported,
            "strategy": default_resolution.strategy,
            "reason": default_resolution.reason,
        }

    runtime_execution = None
    if implementation_id:
        row = await pool.fetchrow(
            "SELECT * FROM executions WHERE implementation_id = $1::uuid "
            "AND procedure_id = $2::uuid AND procedure_version = $3 "
            "ORDER BY started_at DESC LIMIT 1",
            implementation_id, procedure["procedure_id"], procedure["version"],
        )
        runtime_execution = dict(row) if row else None

    return {
        "procedure_row_id": str(procedure["id"]),
        "procedure_id": str(procedure["procedure_id"]),
        "version": procedure["version"],
        "name": procedure["name"],
        "goal": procedure["goal"],
        # Human-facing fields (plan Part 15) -- the machine slug stays as
        # `name`; the UI leads with these.
        "display_name": procedure.get("display_name") or procedure["name"],
        "display_description": procedure.get("display_description") or procedure["goal"],
        "applicability_summary": build_applicability_summary(dict(procedure)),
        "failure_modes": build_failure_modes(dict(procedure)),
        "implementation_id": implementation_id,
        "implementations": implementation_resolutions,
        "runtime_execution": runtime_execution,
        "verification_state": procedure["verification_state"],
        "staleness": procedure["staleness"],
        "availability": procedure["availability"],
        "approval_status": procedure.get("approval_status"),
        "provenance": procedure.get("provenance"),
        "created_by": procedure.get("created_by"),
        "owner_id": procedure.get("owner_id"),
        "visibility": procedure.get("visibility"),
        "scope_type": procedure.get("scope_type"),
        "scope_entity_id": procedure.get("scope_entity_id"),
        "claims": claims,
        "capability": _capability_estimate(evidence),
        "cost": _cost_estimate(procedure),
        "evidence_count": len(evidence),
        # No real source anywhere in this schema for a Solution "license"
        # field -- omitted rather than fabricated, per this module's own
        # docstring rule.
    }
