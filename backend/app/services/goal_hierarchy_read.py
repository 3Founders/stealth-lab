from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any, Optional
from uuid import UUID

from app.services.access import (
    AccessScope,
    TenantScope,
    next_tenant_param_index,
    tenant_predicate,
)
from app.services.goal_abstraction import GoalRelationCycleError, derive_goal_abstraction_state
from app.services.shards import HOME_SHARD, ShardPools, hydrate_rows, pools_for

_LIVE_GOAL_STATUSES = frozenset({"active", "candidate"})

_PROJECTED_GOAL_SOURCE = """
SELECT goal_id, canonical_name, home_shard_id, status, version, scope_type,
       scope_entity_id, visibility, owner_id, resolved_at
FROM goal_search_index
"""

_PROJECTED_GOAL_COLUMNS = """
goal_id::text AS id, home_shard_id, status AS projected_status,
version AS projected_version, scope_type AS projected_scope_type,
scope_entity_id AS projected_scope_entity_id,
visibility::text AS projected_visibility, owner_id AS projected_owner_id,
canonical_name, resolved_at AS projected_resolved_at
"""

_GOAL_SELECT = """
SELECT id::text AS id, canonical_name, description, status,
       visibility::text AS visibility, owner_id, scope_type, scope_entity_id,
       resolved_at, version, t_invalid
FROM goals
WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL
"""


def _uuid(value: str | UUID, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _goal_id(goal: Mapping[str, Any]) -> str:
    value = goal.get("id")
    if value is None:
        value = goal.get("goal_id")
    if value is None:
        raise ValueError("each Goal must have id or goal_id")
    return _uuid(value, "goal_id")


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _scope_key(scope_type: Any, scope_entity_id: Any) -> tuple[str, Optional[str]]:
    normalized = str(scope_type or "global")
    entity = None if normalized == "global" else str(scope_entity_id or "") or None
    return normalized, entity


def _goal_visibility_predicate(
    scope: AccessScope, alias: str = "", param_index: int = 1
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    if scope.is_unrestricted:
        return "TRUE", []
    if scope.viewer_id is None:
        return f"{prefix}visibility = 'public'", []
    if not scope.include_private:
        return f"{prefix}visibility = 'public'", []
    clauses = [f"{prefix}visibility = 'public'", f"{prefix}owner_id = ${param_index}"]
    params: list[Any] = [scope.viewer_id]
    if scope.org_ids:
        index = param_index + 1
        clauses.append(
            f"({prefix}visibility = 'org' AND {prefix}scope_type = 'organization' "
            f"AND {prefix}scope_entity_id = ANY(${index}::text[]))"
        )
        params.append(list(scope.org_ids))
    return "(" + " OR ".join(clauses) + ")", params


def _same_scope(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _scope_key(left.get("scope_type"), left.get("scope_entity_id")) == _scope_key(
        right.get("scope_type"), right.get("scope_entity_id")
    )


def _component_sql(
    access_scope: AccessScope,
    tenant_scope: TenantScope,
) -> tuple[str, tuple[Any, ...]]:
    relation_tenant_sql, relation_params = tenant_predicate(
        tenant_scope, alias="r", param_index=2
    )
    next_index = next_tenant_param_index(tenant_scope, 2)
    visible_sql, visible_params = _goal_visibility_predicate(
        access_scope, alias="g", param_index=next_index
    )
    sql = f"""
    WITH RECURSIVE
    down(goal_id, scope_type, scope_entity_id) AS (
        SELECT g.goal_id, COALESCE(g.scope_type, 'global'),
               CASE WHEN COALESCE(g.scope_type, 'global') = 'global'
                    THEN NULL ELSE g.scope_entity_id END
        FROM ({_PROJECTED_GOAL_SOURCE}) g
        WHERE g.goal_id = ANY($1::uuid[])
          AND g.status IN ('active', 'candidate')
          AND {visible_sql}
        UNION
        SELECT r.abstract_goal_id, COALESCE(r.scope_type, 'global'),
               CASE WHEN COALESCE(r.scope_type, 'global') = 'global'
                    THEN NULL ELSE r.scope_entity_id END
        FROM down d
        JOIN goal_relations r ON r.specific_goal_id = d.goal_id
        JOIN ({_PROJECTED_GOAL_SOURCE}) g ON g.goal_id = r.abstract_goal_id
        WHERE r.relation_type = 'SPECIALIZES'
          AND r.status = 'accepted'
          AND COALESCE(r.scope_type, 'global') = d.scope_type
          AND r.scope_entity_id IS NOT DISTINCT FROM d.scope_entity_id
          AND COALESCE(g.scope_type, 'global') = d.scope_type
          AND g.scope_entity_id IS NOT DISTINCT FROM d.scope_entity_id
          AND g.status IN ('active', 'candidate')
          AND {relation_tenant_sql}
          AND {visible_sql}
    ),
    up(goal_id, scope_type, scope_entity_id) AS (
        SELECT g.goal_id, COALESCE(g.scope_type, 'global'),
               CASE WHEN COALESCE(g.scope_type, 'global') = 'global'
                    THEN NULL ELSE g.scope_entity_id END
        FROM ({_PROJECTED_GOAL_SOURCE}) g
        WHERE g.goal_id = ANY($1::uuid[])
          AND g.status IN ('active', 'candidate')
          AND {visible_sql}
        UNION
        SELECT r.specific_goal_id, COALESCE(r.scope_type, 'global'),
               CASE WHEN COALESCE(r.scope_type, 'global') = 'global'
                    THEN NULL ELSE r.scope_entity_id END
        FROM up u
        JOIN goal_relations r ON r.abstract_goal_id = u.goal_id
        JOIN ({_PROJECTED_GOAL_SOURCE}) g ON g.goal_id = r.specific_goal_id
        WHERE r.relation_type = 'SPECIALIZES'
          AND r.status = 'accepted'
          AND COALESCE(r.scope_type, 'global') = u.scope_type
          AND r.scope_entity_id IS NOT DISTINCT FROM u.scope_entity_id
          AND COALESCE(g.scope_type, 'global') = u.scope_type
          AND g.scope_entity_id IS NOT DISTINCT FROM u.scope_entity_id
          AND g.status IN ('active', 'candidate')
          AND {relation_tenant_sql}
          AND {visible_sql}
    ),
    component(goal_id) AS (
        SELECT goal_id FROM down
        UNION
        SELECT goal_id FROM up
    )
    SELECT {_PROJECTED_GOAL_COLUMNS}
    FROM ({_PROJECTED_GOAL_SOURCE}) g
    WHERE g.goal_id IN (SELECT goal_id FROM component)
      AND g.status IN ('active', 'candidate')
      AND {visible_sql}
    ORDER BY g.canonical_name, g.goal_id
    """
    return sql, (*relation_params, *visible_params)


def _edge_sql(
    access_scope: AccessScope,
    tenant_scope: TenantScope,
) -> tuple[str, tuple[Any, ...]]:
    relation_tenant_sql, relation_params = tenant_predicate(
        tenant_scope, alias="r", param_index=2
    )
    next_index = next_tenant_param_index(tenant_scope, 2)
    specific_sql, specific_params = _goal_visibility_predicate(
        access_scope, alias="s", param_index=next_index
    )
    next_index += len(specific_params)
    abstract_sql, abstract_params = _goal_visibility_predicate(
        access_scope, alias="a", param_index=next_index
    )
    sql = f"""
    SELECT r.specific_goal_id::text AS specific_goal_id,
           r.abstract_goal_id::text AS abstract_goal_id,
           r.relation_type, r.status, r.scope_type, r.scope_entity_id
    FROM goal_relations r
    JOIN ({_PROJECTED_GOAL_SOURCE}) s ON s.goal_id = r.specific_goal_id
    JOIN ({_PROJECTED_GOAL_SOURCE}) a ON a.goal_id = r.abstract_goal_id
    WHERE (r.specific_goal_id = ANY($1::uuid[])
           OR r.abstract_goal_id = ANY($1::uuid[]))
      AND r.relation_type = 'SPECIALIZES'
      AND r.status = 'accepted'
      AND s.status IN ('active', 'candidate')
      AND a.status IN ('active', 'candidate')
      AND COALESCE(r.scope_type, 'global') = COALESCE(s.scope_type, 'global')
      AND r.scope_entity_id IS NOT DISTINCT FROM s.scope_entity_id
      AND COALESCE(r.scope_type, 'global') = COALESCE(a.scope_type, 'global')
      AND r.scope_entity_id IS NOT DISTINCT FROM a.scope_entity_id
      AND {relation_tenant_sql}
      AND {specific_sql}
      AND {abstract_sql}
    ORDER BY r.specific_goal_id, r.abstract_goal_id
    """
    return sql, (*relation_params, *specific_params, *abstract_params)


def _snapshot(
    projected: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = dict(canonical)
    snapshot["id"] = str(canonical["id"])
    snapshot["home_shard_id"] = str(projected.get("home_shard_id") or HOME_SHARD)
    return snapshot


def _hydration_is_current(
    projected: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> bool:
    return (
        canonical.get("t_invalid") is None
        and str(canonical.get("status")) in _LIVE_GOAL_STATUSES
        and str(canonical.get("status")) == str(projected.get("projected_status"))
        and int(canonical.get("version") or 0) == int(projected.get("projected_version") or 0)
        and str(canonical.get("visibility")) == str(projected.get("projected_visibility"))
        and canonical.get("owner_id") == projected.get("projected_owner_id")
        and _scope_key(canonical.get("scope_type"), canonical.get("scope_entity_id"))
        == _scope_key(
            projected.get("projected_scope_type"),
            projected.get("projected_scope_entity_id"),
        )
    )


def _public_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(goal["id"]),
        "canonical_name": goal.get("canonical_name"),
        "description": goal.get("description"),
        "status": goal.get("status"),
        "resolved_at": goal.get("resolved_at"),
    }


def _public_goals(goals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        _public_goal(goal)
        for goal in sorted(goals, key=lambda item: (str(item.get("canonical_name") or ""), str(item["id"])))
    ]


def _descendants(goal_id: str, children: Mapping[str, set[str]]) -> set[str]:
    found: set[str] = set()
    pending = deque(children.get(goal_id, ()))
    while pending:
        current = pending.popleft()
        if current in found or current == goal_id:
            continue
        found.add(current)
        pending.extend(children.get(current, ()))
    return found


async def enrich_goals(
    pool: Any,
    goals: Sequence[Mapping[str, Any]],
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools] = None,
) -> list[dict[str, Any]]:
    """Hierarchy view of each Goal: direct parents/children, derived level,
    coverage, Benchmarks.

    The component (ancestors + descendants), its accepted edges, levels and
    coverage all come from the control database (goal_relations + the goal
    projection, including its derived `resolved_at` copy) -- no shard is read
    for them. Canonical rows are read only for the Goals actually DISPLAYED (the
    requested Goals and their direct parents/children), each batch from its home
    shard; those rows are authoritative for what is shown. A displayed Goal that
    cannot be read or is stale is left out on its own -- it never blanks the page.
    Resolution is per Goal: coverage counts descendants' own `resolved_at`, and
    nothing here resolves anything."""
    goal_ids = _unique([_goal_id(goal) for goal in goals])
    if not goal_ids:
        return []
    component_sql, component_params = _component_sql(access_scope, tenant_scope)
    projected_rows = [dict(row) for row in await pool.fetch(component_sql, goal_ids, *component_params)]
    if not projected_rows:
        return []
    projections = {str(row["id"]): row for row in projected_rows}
    component_ids = list(projections)

    edge_sql, edge_params = _edge_sql(access_scope, tenant_scope)
    edges = [dict(row) for row in await pool.fetch(edge_sql, component_ids, *edge_params)]
    usable_edges = [
        edge
        for edge in edges
        if str(edge["specific_goal_id"]) in projections
        and str(edge["abstract_goal_id"]) in projections
        and _scope_key(projections[str(edge["specific_goal_id"])].get("projected_scope_type"),
                       projections[str(edge["specific_goal_id"])].get("projected_scope_entity_id"))
        == _scope_key(projections[str(edge["abstract_goal_id"])].get("projected_scope_type"),
                      projections[str(edge["abstract_goal_id"])].get("projected_scope_entity_id"))
    ]
    children: dict[str, set[str]] = {goal_id: set() for goal_id in projections}
    parents: dict[str, set[str]] = {goal_id: set() for goal_id in projections}
    for edge in usable_edges:
        specific = str(edge["specific_goal_id"])
        abstract = str(edge["abstract_goal_id"])
        children[abstract].add(specific)
        parents[specific].add(abstract)

    try:
        state_rows = derive_goal_abstraction_state(projections, usable_edges)
    except GoalRelationCycleError:
        return []
    state_by_id = {str(row["goal_id"]): row for row in state_rows}

    # Canonical rows only for what is displayed.
    requested = [goal_id for goal_id in goal_ids if goal_id in projections]
    displayed = set(requested)
    for goal_id in requested:
        displayed |= children[goal_id] | parents[goal_id]
    routes = {goal_id: str(projections[goal_id].get("home_shard_id") or HOME_SHARD) for goal_id in displayed}

    async def fetch(goal_pool: Any, ids: list[str]) -> Sequence[Mapping[str, Any]]:
        return await goal_pool.fetch(_GOAL_SELECT, ids)

    hydration = await hydrate_rows(pools or pools_for(pool), routes, fetch)
    snapshots: dict[str, dict[str, Any]] = {}
    for goal_id in displayed:
        canonical = hydration.rows.get(goal_id)
        if canonical is not None and _hydration_is_current(projections[goal_id], canonical):
            snapshots[goal_id] = _snapshot(projections[goal_id], canonical)

    benchmark_sql = """
    SELECT *
    FROM benchmarks
    WHERE goal_id = ANY($1::uuid[])
    ORDER BY goal_id, version DESC, created_at DESC, id
    """
    visible_goal_ids = [goal_id for goal_id in requested if goal_id in snapshots]
    from app.services.product_model import _row as _benchmark_row

    # Same decoding every other Benchmark reader applies (legacy rows hold
    # JSON-string jsonb values), so the Goal page gets objects, not strings.
    benchmark_rows = [
        _benchmark_row(row) for row in await pool.fetch(benchmark_sql, visible_goal_ids)
    ]
    benchmarks_by_goal: dict[str, list[dict[str, Any]]] = {}
    for row in benchmark_rows:
        goal_id = str(row["goal_id"])
        if goal_id in snapshots:
            benchmarks_by_goal.setdefault(goal_id, []).append(row)

    result: list[dict[str, Any]] = []
    for goal in goals:
        goal_id = _goal_id(goal)
        if goal_id not in snapshots:
            continue
        state = state_by_id[goal_id]
        descendant_ids = _descendants(goal_id, children)
        neighbours = children[goal_id] | parents[goal_id]
        resolved_count = sum(
            1 for descendant_id in descendant_ids
            if projections[descendant_id].get("projected_resolved_at") is not None
        )
        result.append(
            {
                **dict(goal),
                "specializes": _public_goals(
                    [snapshots[child_id] for child_id in children[goal_id] if child_id in snapshots]
                ),
                "abstracts": _public_goals(
                    [snapshots[parent_id] for parent_id in parents[goal_id] if parent_id in snapshots]
                ),
                "abstraction_level": int(state["abstraction_level"]),
                "benchmarks": benchmarks_by_goal.get(goal_id, []),
                "coverage": {
                    "total_count": len(descendant_ids),
                    "resolved_count": resolved_count,
                    "ratio": resolved_count / len(descendant_ids) if descendant_ids else 0.0,
                },
                # False when a direct parent/child exists (level and coverage count
                # it) but could not be shown (shard unreachable / stale projection):
                # a UI must not present the lists as the whole neighbourhood.
                "hierarchy_complete": all(neighbour in snapshots for neighbour in neighbours),
            }
        )
    return result


async def enrich_goal(
    pool: Any,
    goal: Mapping[str, Any],
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools] = None,
) -> Optional[dict[str, Any]]:
    enriched = await enrich_goals(
        pool,
        (goal,),
        access_scope=access_scope,
        tenant_scope=tenant_scope,
        pools=pools,
    )
    return enriched[0] if enriched else None


__all__ = ["enrich_goal", "enrich_goals"]
