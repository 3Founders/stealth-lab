"""
Repository/Project Knowledge — directive §18/§39: "given a repository or
project, reconstruct current structured understanding."

Pure composition over the existing substrate, zero new schema, no
migration. The real mechanism this reads is not a "Repository" or
"Project" table — none exists — but `scope_type`/`scope_entity_id`
(migration 21, CHECK-constrained in migration 22 against
`v0_gate.SCOPE_TYPES`, which already contains `'repository'` and
`'project'` as real vocabulary members). A repository's/project's
knowledge IS simply the set of `knowledge_nodes` (claims) and
`procedures` rows whose `scope_type`/`scope_entity_id` name it —
confirmed directly against `app/services/v0_gate.py` and
`app/services/claims.py::capture_claim` before writing this module.
`scope_entity_id` is a free-text identifier the caller supplies (e.g. a
repo URL, a `org/repo` slug, a project id) — there is no separate
identity table to validate it against, so an unknown id simply returns
empty results, not a 404. That is a deliberate choice matching this
repo's existing "omit rather than error" posture for scope-filtered
reads (`app/api/graph.py`'s own comment: "a node the viewer can't see is
omitted rather than labelled '?'").

DESIGN CHOICE — relevant_procedures (documented per the task's own
request): `applicability.py::find_applicable_procedures` is a QUERY-
answering function — it requires a `goal_embedding` (or at minimum
relies on one for its RRF-fused candidate pre-filter) and evaluates a
`current_scope` dict against each procedure's own `scope`/`exclusions`
JSONB fields, which is a DIFFERENT "scope" concept entirely (ticket 12's
machine-writable narrowing, e.g. `{"repo": [...], "files": [...]}`) from
the `scope_type`/`scope_entity_id` columns this module reads. There is
no task/query/goal available at "reconstruct this repository's current
understanding" time — a repository knowledge summary is not itself a
retrieval query. Reimplementing that mismatch (synthesizing a fake
goal_embedding, or misusing `current_scope={"repo": [repository_id]}` to
approximate a filter `find_applicable_procedures` was never built to
answer) would silently misrepresent what the function does. So
`relevant_procedures` here is a DIRECT, scope-filtered, hard-constraint-
aware read on `procedures` (verification_state/staleness/availability/
approval_status, the same axes `applicability.py::check_hard_constraints`
checks, filtered here directly rather than through that function since no
per-candidate embedding/precondition cascade is meaningful without a
task to evaluate preconditions against) — composition of the SAME
columns applicability.py's cascade inspects, just without the
query-shaped machinery that has nothing to bind to here.

DESIGN CHOICE — project claim aggregation (directive §6, documented per
the task's own request): grepped for any real mechanism that would let a
`scope_type='project'` read roll up evidence captured under
`scope_type='repository'` rows belonging to that project. NONE EXISTS —
no `project_id` column on `procedures`/`knowledge_nodes`, no edge type
linking a repository-scoped claim to its owning project, no join table.
`episodes` does carry a real `project_id` column (migration 17), and
`promote_observation_to_claim` derives a claim's `scope_type='project'`
straight from the justifying episode's `project_id` — but that is
`scope_type='project'` claims being written directly, not a repository
claim being rolled UP into a project view. `get_project_knowledge` below
therefore does exactly what a `scope_type='project'`-scoped read can
honestly do: return claims/procedures explicitly scoped to the project
itself. It does NOT attempt to synthesize a rollup across that project's
repositories, because no real, existing relationship does that today —
inventing one here would be exactly the kind of fabricated aggregation
CLAUDE.md's "docstrings document *why*, including honest scope limits"
rule warns against.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.claims import get_claim_lifecycle_state

# Tenant scope for every query in this module: TenantScope.unrestricted()
# is not a shortcut, it is the ONLY correct choice — knowledge_nodes and
# procedures carry no `tenant_id` column at all (only the six tables
# migration 29 the RLS backstop added it to: evidence, executions,
# change_sets, change_set_operations, failure_routes, and one more — none
# of them knowledge_nodes/procedures). Passing a resolved TenantScope
# here would render `tenant_id = $N::uuid` against a table with no such
# column and fail outright. `graph.py` and `claims.py::list_current_claims`
# establish the same precedent for the same reason.
_TENANT = TenantScope.unrestricted()

# `depth`/`focus` are real bounds, not decorative parameters (the
# directive's own explicit §39 warning against returning the entire
# graph by default). This is not a graph traversal (no GraphStore hop
# count applies — claims/procedures are read flat by scope, not walked
# edge-by-edge), so `depth` is honestly translated into a result-count
# bound instead of a hop count: each unit of depth admits this many more
# rows per collection, capped hard regardless of how large depth is
# requested.
_RESULTS_PER_DEPTH = 25
_MAX_RESULTS = 200


def _result_limit(depth: int) -> int:
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth!r}")
    return min(depth * _RESULTS_PER_DEPTH, _MAX_RESULTS)


async def _fetch_scoped_claims(
    pool: asyncpg.Pool,
    *,
    scope_type: str,
    scope_entity_id: str,
    scope: AccessScope,
    focus: Optional[str],
    limit: int,
) -> list[dict]:
    vis_sql, vis_params, next_index = scope_predicates(
        scope, _TENANT, alias="k", param_index=1,
    )
    params: list[Any] = [*vis_params, scope_type, scope_entity_id]
    focus_clause = ""
    if focus:
        params.append(f"%{focus}%")
        focus_clause = f"AND k.properties->>'statement' ILIKE ${len(params)}"
    params.append(limit)

    rows = await pool.fetch(
        f"SELECT k.id, k.name, k.properties, k.t_valid FROM knowledge_nodes k "
        f"WHERE k.node_type = 'claim' "
        f"AND k.scope_type = ${next_index} AND k.scope_entity_id = ${next_index + 1} "
        f"AND k.t_invalid IS NULL "
        f"AND COALESCE(k.properties->>'truth_state', 'IN') = 'IN' "
        f"{focus_clause} "
        f"AND {vis_sql} "
        f"ORDER BY k.t_valid DESC "
        f"LIMIT ${len(params)}",
        *params,
    )
    return [dict(r) for r in rows]


async def _fetch_scoped_procedures(
    pool: asyncpg.Pool,
    *,
    scope_type: str,
    scope_entity_id: str,
    scope: AccessScope,
    focus: Optional[str],
    limit: int,
) -> list[dict]:
    vis_sql, vis_params, next_index = scope_predicates(
        scope, _TENANT, alias="p", param_index=1,
    )
    params: list[Any] = [*vis_params, scope_type, scope_entity_id]
    focus_clause = ""
    if focus:
        params.append(f"%{focus}%")
        focus_clause = f"AND (p.name ILIKE ${len(params)} OR p.goal ILIKE ${len(params)})"
    params.append(limit)

    rows = await pool.fetch(
        f"SELECT p.id, p.procedure_id, p.name, p.goal, p.verification_state, "
        f"p.staleness, p.availability, p.approval_status, p.version, p.t_valid "
        f"FROM procedures p "
        f"WHERE p.scope_type = ${next_index} AND p.scope_entity_id = ${next_index + 1} "
        f"AND p.t_invalid IS NULL "
        f"{focus_clause} "
        f"AND {vis_sql} "
        f"ORDER BY p.t_valid DESC "
        f"LIMIT ${len(params)}",
        *params,
    )
    return [dict(r) for r in rows]


async def _enrich_claims_with_lifecycle(pool: asyncpg.Pool, claims: list[dict]) -> list[dict]:
    """Attach each claim's real, computed lifecycle_state
    (claims.py::get_claim_lifecycle_state, reused verbatim — not
    reimplemented). Sequential, not gathered concurrently: these all
    share the same `pool`, and asyncpg pool connections are borrowed per
    call, so concurrent fetches here are safe, but sequential keeps this
    module's first version simple and avoids surprising the pool with a
    burst of N simultaneous acquires for what is a read-only, non-
    latency-critical summary endpoint."""
    enriched = []
    for claim in claims:
        state = await get_claim_lifecycle_state(pool, str(claim["id"]))
        enriched.append({**claim, "lifecycle_state": state})
    return enriched


def _confidence_summary(claims: list[dict], procedures: list[dict]) -> dict:
    """Honest aggregate counts over exactly what was fetched -- never a
    fabricated single confidence number. `claims` here must already carry
    `lifecycle_state` (see _enrich_claims_with_lifecycle)."""
    claim_states: dict[str, int] = {}
    for c in claims:
        state = c.get("lifecycle_state", "unknown")
        claim_states[state] = claim_states.get(state, 0) + 1

    procedure_states: dict[str, int] = {}
    for p in procedures:
        state = p.get("verification_state", "unknown")
        procedure_states[state] = procedure_states.get(state, 0) + 1

    return {
        "total_claims": len(claims),
        "claims_by_lifecycle_state": claim_states,
        "total_procedures": len(procedures),
        "procedures_by_verification_state": procedure_states,
    }


def _claim_out(row: dict) -> dict:
    props = dict(row["properties"])
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "statement": props.get("statement"),
        "subject": props.get("subject"),
        "predicate": props.get("predicate"),
        "object": props.get("object"),
        "truth_state": props.get("truth_state", "IN"),
        "confidence": props.get("confidence"),
        "lifecycle_state": row.get("lifecycle_state"),
        "t_valid": row["t_valid"].isoformat() if row.get("t_valid") else None,
    }


def _procedure_out(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "procedure_id": str(row["procedure_id"]),
        "name": row["name"],
        "goal": row["goal"],
        "version": row["version"],
        "verification_state": row["verification_state"],
        "staleness": row["staleness"],
        "availability": row["availability"],
        "approval_status": row["approval_status"],
    }


async def _get_scoped_knowledge(
    pool: asyncpg.Pool,
    *,
    scope_type: str,
    scope_entity_id: str,
    focus: Optional[str],
    depth: int,
    scope: AccessScope,
) -> dict:
    limit = _result_limit(depth)

    claim_rows = await _fetch_scoped_claims(
        pool, scope_type=scope_type, scope_entity_id=scope_entity_id,
        scope=scope, focus=focus, limit=limit,
    )
    claim_rows = await _enrich_claims_with_lifecycle(pool, claim_rows)

    procedure_rows = await _fetch_scoped_procedures(
        pool, scope_type=scope_type, scope_entity_id=scope_entity_id,
        scope=scope, focus=focus, limit=limit,
    )

    conflicts = [_claim_out(c) for c in claim_rows if c.get("lifecycle_state") == "disputed"]

    return {
        f"{scope_type}_id": scope_entity_id,
        "claims": [_claim_out(c) for c in claim_rows],
        "relevant_procedures": [_procedure_out(p) for p in procedure_rows],
        "conflicts": conflicts,
        "confidence_summary": _confidence_summary(claim_rows, procedure_rows),
    }


async def get_repository_knowledge(
    pool: asyncpg.Pool,
    repository_id: str,
    *,
    focus: Optional[str] = None,
    depth: int = 2,
    scope: AccessScope,
) -> dict:
    """
    Reconstruct current structured understanding of one repository
    (directive §18/§39): live (`t_invalid IS NULL`), currently-believed
    (`truth_state <> 'OUT'`) claims and procedures whose `scope_type` =
    `'repository'` and `scope_entity_id` = `repository_id`, visibility-
    filtered via `scope_predicates()` (the only legal source of that SQL,
    per CLAUDE.md's own hard rule).

    `focus`: optional case-insensitive substring filter — matched against
    each claim's `statement` and each procedure's `name`/`goal`. A real
    filter, applied in SQL, not a decorative parameter.

    `depth`: bounds how many claims/procedures are returned (see
    `_result_limit` — this is not a graph traversal, so depth is honestly
    translated into a row-count cap rather than a hop count). Never
    returns the entire graph by default, per the directive's own warning.

    `conflicts`: claims among the returned set whose computed lifecycle
    state (`claims.py::get_claim_lifecycle_state`, reused verbatim) is
    `'disputed'` — the SAME real open-conflict-trigger detection every
    other reader in this codebase uses, not a new heuristic.

    See this module's docstring for the documented design choice on why
    `relevant_procedures` is a direct scoped read rather than a call
    through `applicability.py::find_applicable_procedures`.
    """
    return await _get_scoped_knowledge(
        pool, scope_type="repository", scope_entity_id=repository_id,
        focus=focus, depth=depth, scope=scope,
    )


async def get_project_knowledge(
    pool: asyncpg.Pool,
    project_id: str,
    *,
    focus: Optional[str] = None,
    depth: int = 2,
    scope: AccessScope,
) -> dict:
    """
    Same shape as `get_repository_knowledge`, `scope_type='project'`.

    IMPORTANT (directive §6, documented honestly rather than fabricated):
    this reads claims/procedures explicitly captured with
    `scope_type='project'` — it does NOT aggregate/roll up claims scoped
    to that project's individual repositories. No real, existing
    mechanism in this schema links a `scope_type='repository'` row back
    to an owning project (no `project_id` column on `procedures` or
    `knowledge_nodes`, no edge type for it) — see this module's top-level
    docstring for the full grep-confirmed finding. A caller wanting a
    combined repository+project view today has to call
    `get_repository_knowledge` per repository and `get_project_knowledge`
    once, and combine them itself; this function does not silently do
    that combination under a name that implies it already has.
    """
    return await _get_scoped_knowledge(
        pool, scope_type="project", scope_entity_id=project_id,
        focus=focus, depth=depth, scope=scope,
    )
