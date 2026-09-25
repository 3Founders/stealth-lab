"""Accepted Goal specialization edges as a scoped, cycle-safe abstraction DAG.

``goal_relations`` remains the canonical relation store. This module admits
only same-scope accepted edges, treats proposed and rejected rows as
non-routing evidence, and materializes only rebuildable scalar state. It never
writes Goal lifecycle or execution fields.
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from app.services.access import (
    AccessScope,
    TenantScope,
    next_tenant_param_index,
    scope_predicates,
    tenant_predicate,
    tenant_transaction,
)
from app.services.shards import HOME_SHARD, HydrationResult, ShardPools, hydrate_rows, pools_for

RELATION_TYPE = "SPECIALIZES"
LIVE_GOAL_STATUSES = ("active", "candidate")
RELATION_STATUSES = frozenset({"proposed", "accepted", "rejected"})
RELATION_POLICY = "goal_abstraction_auto_acceptance"
RELATION_POLICY_VERSION = "goal_abstraction_placement_v1"
RELATION_AUTHORITY = "goal_abstraction_ingestion_worker"
NeighborDirection = Literal["parents", "children", "both"]
_COMMONS_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_UNSET = object()

_GOAL_SELECT = """
SELECT id::text AS id, canonical_name, description, status, scope_type,
       scope_entity_id, visibility::text AS visibility, owner_id,
       resolved_at, version, home_shard_id, t_invalid
FROM goals
WHERE id = ANY($1::uuid[])
"""

_PROJECTED_GOAL_SOURCE = f"""
SELECT goal_id, canonical_name, home_shard_id, status, version, scope_type,
       scope_entity_id, visibility, owner_id,
       CASE
           WHEN scope_type = 'organization'
            AND scope_entity_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               THEN scope_entity_id::uuid
           ELSE COALESCE(tenant_id, '{_COMMONS_TENANT_ID}'::uuid)
       END AS tenant_id
FROM goal_search_index
"""

_PROJECTED_GOAL_COLUMNS = """
goal_id::text AS id, home_shard_id, status AS projected_status, version AS projected_version,
scope_type AS projected_scope_type, scope_entity_id AS projected_scope_entity_id,
visibility::text AS projected_visibility, owner_id AS projected_owner_id,
tenant_id, canonical_name
"""

_PROJECTED_NEIGHBOR_COLUMNS = """
n.goal_id::text AS neighbor_id, n.home_shard_id, n.status AS projected_status,
n.version AS projected_version, n.scope_type AS projected_scope_type,
n.scope_entity_id AS projected_scope_entity_id, n.visibility::text AS projected_visibility,
n.owner_id AS projected_owner_id, n.tenant_id, n.canonical_name
"""

_RELATION_RETURN = """
RETURNING specific_goal_id::text AS specific_goal_id,
abstract_goal_id::text AS abstract_goal_id, relation_type, status, confidence,
provenance, decision_id::text AS decision_id, decision_metadata,
decided_by, decided_at, scope_type, scope_entity_id, tenant_id::text AS tenant_id,
created_at, updated_at
"""


class GoalAbstractionError(ValueError):
    """Base error for rejected hierarchy operations."""


class GoalRelationSelfError(GoalAbstractionError):
    """A Goal cannot specialize itself."""


class GoalRelationScopeError(GoalAbstractionError):
    """Accepted edges must connect Goals in one semantic scope."""


class GoalRelationRedundancyError(GoalAbstractionError):
    """The edge is already implied by a longer accepted path."""


class GoalRelationCycleError(GoalAbstractionError):
    """The candidate edge would close or encounter an accepted cycle."""

    def __init__(self, involved: Iterable[str]):
        self.involved = tuple(sorted({str(item) for item in involved}))
        super().__init__("accepted Goal relations would contain a cycle: " + ", ".join(self.involved))


class GoalRelationVisibilityError(GoalAbstractionError):
    """At least one endpoint is missing, stale, or invisible to the viewer."""


class GoalRelationStatusConflict(GoalAbstractionError):
    """A locked relation no longer has the caller's expected status."""


class GoalRelationDependencyError(RuntimeError):
    """A canonical Goal could not be hydrated completely."""

    def __init__(self, unavailable_shards: Mapping[str, str], missing_ids: Sequence[str]):
        self.unavailable_shards = dict(unavailable_shards)
        self.missing_ids = tuple(missing_ids)
        super().__init__(
            "Goal relation endpoints are incomplete: "
            f"unavailable_shards={self.unavailable_shards!r}, missing_ids={self.missing_ids!r}"
        )


def _uuid(value: str | UUID, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _optional_uuid(value: str | UUID | None, field: str) -> Optional[str]:
    return None if value is None else _uuid(value, field)


def _unique_ids(values: Iterable[str | UUID]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _uuid(value, "goal_id")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _scope_key(scope_type: Optional[str], scope_entity_id: Optional[str]) -> tuple[str, Optional[str]]:
    normalized_type = scope_type or "global"
    return normalized_type, None if normalized_type == "global" else scope_entity_id


def _scope_label(scope_type: Optional[str], scope_entity_id: Optional[str]) -> str:
    normalized_type, normalized_entity = _scope_key(scope_type, scope_entity_id)
    return normalized_type if normalized_entity is None else f"{normalized_type}:{normalized_entity}"


def _same_optional(left: Any, right: Any, *, uuid_value: bool = False) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if uuid_value:
        return str(left) == str(right)
    return left == right


def _validate_confidence(confidence: Optional[float]) -> Optional[float]:
    if confidence is None:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number between 0 and 1")
    value = float(confidence)
    if value < 0 or value > 1 or value != value or value in (float("inf"), float("-inf")):
        raise ValueError("confidence must be a finite number between 0 and 1")
    return value


def _validate_text(value: Optional[str], field: str, *, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    normalized = str(value).strip()
    if required and not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > 500:
        raise ValueError(f"{field} must be at most 500 characters")
    return normalized


def _validate_decision_metadata(value: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("decision_metadata must be a JSON object")
    metadata = dict(value)
    try:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("decision_metadata must contain finite JSON values") from exc
    if len(encoded.encode("utf-8")) > 65_536:
        raise ValueError("decision_metadata must be at most 65536 bytes")
    return metadata


def _accepted_relations(
    relations: Iterable[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for relation in relations:
        if relation.get("relation_type") != RELATION_TYPE:
            continue
        if relation.get("status") != "accepted":
            continue
        specific = _uuid(relation["specific_goal_id"], "specific_goal_id")
        abstract = _uuid(relation["abstract_goal_id"], "abstract_goal_id")
        edges.append((specific, abstract))
    return edges


def is_transitively_redundant(
    specific_goal_id: str | UUID,
    abstract_goal_id: str | UUID,
    relations: Iterable[Mapping[str, Any]],
) -> bool:
    """Whether an accepted path already implies the candidate direct edge."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    if specific == abstract:
        return False
    adjacency: dict[str, set[str]] = {}
    for source, target in _accepted_relations(relations):
        if source == specific and target == abstract:
            continue
        adjacency.setdefault(source, set()).add(target)
    pending = [specific]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == abstract:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def validate_goal_relation_graph(
    specific_goal_id: str | UUID,
    abstract_goal_id: str | UUID,
    relations: Iterable[Mapping[str, Any]],
) -> None:
    """Reject self, alternate-path redundancy, and accepted cycles iteratively."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    if specific == abstract:
        raise GoalRelationSelfError("a Goal cannot specialize itself")
    edges = _accepted_relations(relations)
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        if source == target:
            raise GoalRelationCycleError([source])
        if source == specific and target == abstract:
            continue
        adjacency.setdefault(source, set()).add(target)
    if _reachable(adjacency, abstract, stop=specific):
        raise GoalRelationCycleError([specific, abstract])
    if _reachable(adjacency, specific, stop=abstract):
        raise GoalRelationRedundancyError(
            f"Goal {specific} already reaches {abstract} through another accepted edge"
        )


def _reachable(
    adjacency: Mapping[str, Iterable[str]], start: str, *, stop: Optional[str] = None
) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == stop:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def derive_abstraction_levels(
    goal_ids: Iterable[str | UUID], relations: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    """Derive root=0 and child=1+max(parent) levels without recursion."""
    nodes = set(_unique_ids(goal_ids))
    parents = {goal_id: set() for goal_id in nodes}
    children = {goal_id: set() for goal_id in nodes}
    for source, target in _accepted_relations(relations):
        if source not in nodes or target not in nodes:
            continue
        if source == target:
            raise GoalRelationCycleError([source])
        parents[source].add(target)
        children[target].add(source)
    remaining = {goal_id: len(parents[goal_id]) for goal_id in nodes}
    levels = {goal_id: 0 for goal_id in nodes}
    ready = deque(sorted(goal_id for goal_id, count in remaining.items() if count == 0))
    processed = 0
    while ready:
        parent = ready.popleft()
        processed += 1
        for child in sorted(children[parent]):
            levels[child] = max(levels[child], levels[parent] + 1)
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    if processed != len(nodes):
        raise GoalRelationCycleError(
            goal_id for goal_id, count in remaining.items() if count > 0
        )
    return levels


def derive_goal_abstraction_state(
    goal_ids: Iterable[str | UUID],
    relations: Iterable[Mapping[str, Any]],
    *,
    direct_resolved_at: Optional[Mapping[str | UUID, Any]] = None,
) -> list[dict[str, Any]]:
    """Build scalar DAG state and per-root coverage without storing a closure."""
    nodes = set(_unique_ids(goal_ids))
    levels = derive_abstraction_levels(nodes, relations)
    parents: dict[str, set[str]] = {goal_id: set() for goal_id in nodes}
    children: dict[str, set[str]] = {goal_id: set() for goal_id in nodes}
    for source, target in _accepted_relations(relations):
        if source in nodes and target in nodes:
            parents[source].add(target)
            children[target].add(source)
    resolved = {
        _uuid(goal_id, "goal_id"): resolved_at
        for goal_id, resolved_at in (direct_resolved_at or {}).items()
        if _uuid(goal_id, "goal_id") in nodes
    }
    state: list[dict[str, Any]] = []
    for goal_id in sorted(nodes):
        pending = list(children[goal_id])
        descendants: set[str] = set()
        while pending:
            descendant = pending.pop()
            if descendant in descendants:
                continue
            descendants.add(descendant)
            pending.extend(children[descendant])
        total = len(descendants)
        resolved_count = sum(1 for descendant in descendants if resolved.get(descendant) is not None)
        state.append(
            {
                "goal_id": goal_id,
                "abstraction_level": levels[goal_id],
                "parent_count": len(parents[goal_id]),
                "direct_child_count": len(children[goal_id]),
                "coverage_total_count": total,
                "coverage_resolved_count": resolved_count,
                "coverage_ratio": resolved_count / total if total else 0.0,
                "direct_resolved_at": resolved.get(goal_id),
            }
        )
    return state


def _projected_goal_rows(sql: str) -> str:
    return f"SELECT {_PROJECTED_GOAL_COLUMNS} FROM ({_PROJECTED_GOAL_SOURCE}) g WHERE {sql}"


async def _hydrate_projected_goals(
    pool: Any,
    projected_rows: Sequence[Mapping[str, Any]],
    *,
    pools: Optional[ShardPools],
) -> tuple[dict[str, dict[str, Any]], HydrationResult]:
    projected = {str(row["id"]): dict(row) for row in projected_rows}
    if not projected:
        return {}, HydrationResult()
    routes = {
        goal_id: str(row.get("home_shard_id") or HOME_SHARD)
        for goal_id, row in projected.items()
    }

    async def fetch(goal_pool: Any, goal_ids: list[str]) -> Sequence[Mapping[str, Any]]:
        return await goal_pool.fetch(_GOAL_SELECT, goal_ids)

    hydration = await hydrate_rows(pools or pools_for(pool), routes, fetch)
    snapshots: dict[str, dict[str, Any]] = {}
    for goal_id, projection in projected.items():
        source = hydration.rows.get(goal_id)
        if source is None or source.get("t_invalid") is not None:
            continue
        if str(source.get("status")) not in LIVE_GOAL_STATUSES:
            continue
        if str(projection.get("projected_status")) != str(source.get("status")):
            continue
        if int(projection.get("projected_version") or 0) != int(source.get("version") or 0):
            continue
        projected_scope = _scope_key(
            projection.get("projected_scope_type"), projection.get("projected_scope_entity_id")
        )
        source_scope = _scope_key(source.get("scope_type"), source.get("scope_entity_id"))
        if projected_scope != source_scope:
            continue
        if str(projection.get("projected_visibility")) != str(source.get("visibility")):
            continue
        if projection.get("projected_owner_id") != source.get("owner_id"):
            continue
        snapshot = dict(source)
        snapshot["id"] = goal_id
        snapshot["tenant_id"] = projection.get("tenant_id")
        snapshot["home_shard_id"] = routes[goal_id]
        snapshot["canonical_name"] = source.get("canonical_name") or projection.get("canonical_name")
        snapshots[goal_id] = snapshot
    return snapshots, hydration


async def _visible_goal_snapshots(
    pool: Any,
    goal_ids: Iterable[str | UUID],
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools],
) -> tuple[dict[str, dict[str, Any]], HydrationResult]:
    ids = _unique_ids(goal_ids)
    if not ids:
        return {}, HydrationResult()
    scope_sql, scope_params, _ = scope_predicates(
        access_scope, tenant_scope, alias="g", param_index=2
    )
    rows = await pool.fetch(
        _projected_goal_rows(
            f"g.goal_id = ANY($1::uuid[]) AND g.status IN ('active', 'candidate') AND {scope_sql}"
        ),
        ids,
        *scope_params,
    )
    return await _hydrate_projected_goals(pool, rows, pools=pools)


def _public_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(goal["id"]),
        "canonical_name": goal.get("canonical_name"),
        "description": goal.get("description"),
        "status": goal.get("status"),
        "scope_type": goal.get("scope_type") or "global",
        "scope_entity_id": goal.get("scope_entity_id"),
        "visibility": str(goal.get("visibility")),
        "owner_id": goal.get("owner_id"),
        "resolved_at": goal.get("resolved_at"),
        "version": goal.get("version"),
        "tenant_id": str(goal["tenant_id"]) if goal.get("tenant_id") is not None else None,
        "home_shard_id": goal.get("home_shard_id") or HOME_SHARD,
    }


async def adjudicate_goal_relation(
    pool: Any,
    specific_goal_id: str | UUID,
    abstract_goal_id: str | UUID,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """Authorize both endpoints and prove they share one semantic scope."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    if specific == abstract:
        raise GoalRelationSelfError("a Goal cannot specialize itself")
    snapshots, hydration = await _visible_goal_snapshots(
        pool,
        (specific, abstract),
        access_scope=access_scope,
        tenant_scope=tenant_scope,
        pools=pools,
    )
    if hydration.partial or specific not in snapshots or abstract not in snapshots:
        raise GoalRelationVisibilityError("both Goal endpoints must be visible and fully hydrated")
    specific_goal = snapshots[specific]
    abstract_goal = snapshots[abstract]
    specific_scope = _scope_key(
        specific_goal.get("scope_type"), specific_goal.get("scope_entity_id")
    )
    abstract_scope = _scope_key(
        abstract_goal.get("scope_type"), abstract_goal.get("scope_entity_id")
    )
    if specific_scope != abstract_scope:
        raise GoalRelationScopeError(
            "Goal relations require the same scope: "
            f"{_scope_label(*specific_scope)} != {_scope_label(*abstract_scope)}"
        )
    return {
        "specific_goal": _public_goal(specific_goal),
        "abstract_goal": _public_goal(abstract_goal),
        "scope_type": specific_scope[0],
        "scope_entity_id": specific_scope[1],
    }


async def _validate_accepted_edge(conn: Any, specific: str, abstract: str) -> None:
    cycle = await conn.fetchval(
        """
        WITH RECURSIVE ancestors(goal_id) AS (
            SELECT $2::uuid
            UNION
            SELECT r.abstract_goal_id
            FROM goal_relations r
            JOIN ancestors a ON r.specific_goal_id = a.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
        )
        SELECT EXISTS (SELECT 1 FROM ancestors WHERE goal_id = $1::uuid)
        """,
        specific,
        abstract,
    )
    if cycle:
        raise GoalRelationCycleError([specific, abstract])
    redundant = await conn.fetchval(
        """
        WITH RECURSIVE descendants(goal_id) AS (
            SELECT r.specific_goal_id
            FROM goal_relations r
            WHERE r.abstract_goal_id = $2::uuid
              AND r.relation_type = 'SPECIALIZES'
              AND r.status = 'accepted'
              AND NOT (r.specific_goal_id = $1::uuid AND r.abstract_goal_id = $2::uuid)
            UNION
            SELECT r.specific_goal_id
            FROM goal_relations r
            JOIN descendants d ON r.abstract_goal_id = d.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
              AND NOT (r.specific_goal_id = $1::uuid AND r.abstract_goal_id = $2::uuid)
        )
        SELECT EXISTS (SELECT 1 FROM descendants WHERE goal_id = $1::uuid)
        """,
        specific,
        abstract,
    )
    if redundant:
        raise GoalRelationRedundancyError(
            f"Goal {specific} already reaches {abstract} through another accepted edge"
        )


async def is_accepted_edge_redundant(
    pool: Any, specific_goal_id: str | UUID, abstract_goal_id: str | UUID
) -> bool:
    """Whether another accepted path already carries specific -> abstract, so
    the direct edge adds nothing (the graph keeps direct edges only)."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    return bool(await pool.fetchval(
        """
        WITH RECURSIVE reach(goal_id) AS (
            SELECT r.abstract_goal_id
            FROM goal_relations r
            WHERE r.specific_goal_id = $1::uuid
              AND r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
              AND r.abstract_goal_id <> $2::uuid
            UNION
            SELECT r.abstract_goal_id
            FROM goal_relations r
            JOIN reach ON r.specific_goal_id = reach.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
        )
        SELECT EXISTS (SELECT 1 FROM reach WHERE goal_id = $2::uuid)
        """,
        specific,
        abstract,
    ))


def _relation_result(
    row: Mapping[str, Any], *, created: bool, persisted: bool
) -> dict[str, Any]:
    return {
        "specific_goal_id": str(row["specific_goal_id"]),
        "abstract_goal_id": str(row["abstract_goal_id"]),
        "relation_type": str(row["relation_type"]),
        "status": str(row["status"]),
        "confidence": row.get("confidence"),
        "provenance": row.get("provenance"),
        "decision_id": str(row["decision_id"]) if row.get("decision_id") else None,
        "decision_metadata": row.get("decision_metadata") or {},
        "decided_by": row.get("decided_by"),
        "decided_at": row.get("decided_at"),
        "scope_type": row.get("scope_type"),
        "scope_entity_id": row.get("scope_entity_id"),
        "tenant_id": str(row["tenant_id"]) if row.get("tenant_id") else None,
        "created": created,
        "changed": persisted and not created,
        "persisted": persisted,
    }


def _relation_is_unchanged(
    existing: Mapping[str, Any],
    *,
    status: str,
    confidence: Optional[float],
    provenance: Optional[str],
    decision_id: Optional[str],
    decision_metadata: Optional[dict[str, Any]],
    decided_by: Optional[str],
    scope_type: Optional[str],
    scope_entity_id: Optional[str],
    tenant_id: Optional[str],
) -> bool:
    return (
        str(existing["status"]) == status
        and _same_optional(existing.get("confidence"), confidence)
        and existing.get("provenance") == provenance
        and _same_optional(existing.get("decision_id"), decision_id, uuid_value=True)
        and (existing.get("decision_metadata") or {}) == (decision_metadata or {})
        and existing.get("decided_by") == decided_by
        and _same_optional(existing.get("scope_type"), scope_type)
        and _same_optional(existing.get("scope_entity_id"), scope_entity_id)
        and _same_optional(existing.get("tenant_id"), tenant_id, uuid_value=True)
    )


async def persist_goal_relation(
    pool: Any,
    specific_goal_id: str | UUID,
    abstract_goal_id: str | UUID,
    *,
    status: str,
    provenance: str,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    confidence: Optional[float] = None,
    decision_id: str | UUID | None = None,
    decision_metadata: Optional[Mapping[str, Any]] = None,
    decided_by: Optional[str] = None,
    expected_status: Any = _UNSET,
    pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """Persist one relation idempotently after scope and graph adjudication."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    if specific == abstract:
        raise GoalRelationSelfError("a Goal cannot specialize itself")
    if status not in RELATION_STATUSES:
        raise ValueError(f"status must be one of {sorted(RELATION_STATUSES)}")
    if expected_status is not _UNSET and expected_status not in RELATION_STATUSES and expected_status is not None:
        raise ValueError("expected_status must be a relation status, None, or unset")
    normalized_provenance = _validate_text(provenance, "provenance", required=True)
    normalized_decision_id = _optional_uuid(decision_id, "decision_id")
    normalized_metadata = _validate_decision_metadata(decision_metadata)
    normalized_decided_by = _validate_text(decided_by, "decided_by", required=False)
    normalized_confidence = _validate_confidence(confidence)
    adjudication = await adjudicate_goal_relation(
        pool,
        specific,
        abstract,
        access_scope=access_scope,
        tenant_scope=tenant_scope,
        pools=pools,
    )
    endpoint_scopes = {
        (
            _scope_key(
                adjudication["specific_goal"].get("scope_type"),
                adjudication["specific_goal"].get("scope_entity_id"),
            ),
            _scope_key(
                adjudication["abstract_goal"].get("scope_type"),
                adjudication["abstract_goal"].get("scope_entity_id"),
            ),
        )
    }
    same_scope = len(endpoint_scopes) == 1
    if status == "accepted" and not same_scope:
        raise GoalRelationScopeError("accepted Goal relations require the same scope")
    if status in {"accepted", "rejected"}:
        if normalized_decision_id is None and normalized_decided_by is None:
            raise ValueError("accepted or rejected relations require decision_id or decided_by")
        if not normalized_metadata:
            raise ValueError("accepted or rejected relations require decision metadata")
    scope_type: Optional[str]
    scope_entity_id: Optional[str]
    if same_scope:
        scope_type = adjudication["scope_type"]
        scope_entity_id = adjudication["scope_entity_id"]
    else:
        scope_type = None
        scope_entity_id = None
    relation_tenant = tenant_scope.tenant_id
    # The pool's jsonb codec (app/db/session.py) encodes Python values itself;
    # passing a pre-serialized string would be stored as a JSON *string* and
    # fail goal_relations_decision_metadata_object_chk on every decision.
    metadata_json = dict(normalized_metadata or {})
    projection_refresh_required = status in {"accepted", "rejected"}
    async with tenant_transaction(pool, tenant_scope) as conn:
        lock_scope = scope_type or ":".join(sorted((specific, abstract)))
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"goal-abstraction:{relation_tenant or '*'}:{lock_scope}",
        )
        existing = await conn.fetchrow(
            """
            SELECT specific_goal_id::text AS specific_goal_id,
                   abstract_goal_id::text AS abstract_goal_id, relation_type, status,
                   confidence, provenance, decision_id::text AS decision_id,
                   decision_metadata, decided_by, decided_at, scope_type,
                   scope_entity_id, tenant_id::text AS tenant_id, created_at, updated_at
            FROM goal_relations
            WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid
              AND relation_type = 'SPECIALIZES'
            FOR UPDATE
            """,
            specific,
            abstract,
        )
        if expected_status is not _UNSET:
            actual_status = None if existing is None else str(existing.get("status"))
            if actual_status != expected_status:
                raise GoalRelationStatusConflict(
                    "Goal relation status changed: "
                    f"expected {expected_status!r}, found {actual_status!r}"
                )
        if existing is not None and str(existing.get("status")) == "accepted":
            projection_refresh_required = True
        if existing is not None and _relation_is_unchanged(
            existing,
            status=status,
            confidence=normalized_confidence,
            provenance=normalized_provenance,
            decision_id=normalized_decision_id,
            decision_metadata=normalized_metadata,
            decided_by=normalized_decided_by,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            tenant_id=relation_tenant,
        ):
            result = _relation_result(existing, created=False, persisted=False)
        else:
            if status == "accepted":
                await _validate_accepted_edge(conn, specific, abstract)
            decided_at = (
                datetime.now(timezone.utc)
                if status in {"accepted", "rejected"} or normalized_decided_by is not None
                else None
            )
            if existing is None:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO goal_relations (
                        specific_goal_id, abstract_goal_id, relation_type, status, confidence,
                        provenance, decision_id, decision_metadata, decided_by, decided_at,
                        scope_type, scope_entity_id, tenant_id
                    ) VALUES (
                        $1::uuid, $2::uuid, 'SPECIALIZES', $3, $4,
                        $5, $6::uuid, $7::jsonb, $8, $9,
                        $10, $11, $12::uuid
                    )
                    ON CONFLICT (specific_goal_id, abstract_goal_id, relation_type) DO NOTHING
                    {_RELATION_RETURN}
                    """,
                    specific,
                    abstract,
                    status,
                    normalized_confidence,
                    normalized_provenance,
                    normalized_decision_id,
                    metadata_json,
                    normalized_decided_by,
                    decided_at,
                    scope_type,
                    scope_entity_id,
                    relation_tenant,
                )
                if row is None:
                    current = await conn.fetchrow(
                        """
                        SELECT status
                        FROM goal_relations
                        WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid
                          AND relation_type = 'SPECIALIZES'
                        FOR UPDATE
                        """,
                        specific,
                        abstract,
                    )
                    actual_status = None if current is None else str(current.get("status"))
                    raise GoalRelationStatusConflict(
                        "Goal relation was created concurrently: "
                        f"expected absent, found {actual_status!r}"
                    )
                result = _relation_result(row, created=True, persisted=True)
            else:
                previous_status = str(existing["status"])
                row = await conn.fetchrow(
                    f"""
                    UPDATE goal_relations
                    SET status = $3, confidence = $4, provenance = $5, decision_id = $6::uuid,
                        decision_metadata = $7::jsonb, decided_by = $8, decided_at = $9,
                        scope_type = $10, scope_entity_id = $11, tenant_id = $12::uuid,
                        updated_at = now()
                    WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid
                      AND relation_type = 'SPECIALIZES'
                      AND status = $13::text
                    {_RELATION_RETURN}
                    """,
                    specific,
                    abstract,
                    status,
                    normalized_confidence,
                    normalized_provenance,
                    normalized_decision_id,
                    metadata_json,
                    normalized_decided_by,
                    decided_at,
                    scope_type,
                    scope_entity_id,
                    relation_tenant,
                    previous_status,
                )
                if row is None:
                    raise GoalRelationStatusConflict(
                        f"Goal relation status changed from {previous_status!r}"
                    )
                result = _relation_result(row, created=False, persisted=True)
    if projection_refresh_required:
        result["projection"] = await recompute_affected_goal_abstraction_state(
            pool,
            specific,
            abstract,
            access_scope=access_scope,
            tenant_scope=tenant_scope,
            pools=pools,
        )
    return result


async def expand_goal_neighbors(
    pool: Any,
    goal_id: str | UUID,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    direction: NeighborDirection = "both",
    limit: int = 100,
    pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """Return one-hop accepted parents and children visible to the viewer."""
    if direction not in {"parents", "children", "both"}:
        raise ValueError("direction must be parents, children, or both")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    anchor_id = _uuid(goal_id, "goal_id")
    anchor_rows, anchor_hydration = await _visible_goal_snapshots(
        pool,
        (anchor_id,),
        access_scope=access_scope,
        tenant_scope=tenant_scope,
        pools=pools,
    )
    anchor = anchor_rows.get(anchor_id)
    if anchor is None or anchor_hydration.partial:
        return {
            "goal": None,
            "parents": [],
            "children": [],
            "partial": anchor_hydration.partial,
            "unavailable_shards": dict(anchor_hydration.unavailable_shards),
            "missing_ids": list(anchor_hydration.missing_ids),
        }
    anchor_scope_type, anchor_scope_entity_id = _scope_key(
        anchor.get("scope_type"), anchor.get("scope_entity_id")
    )
    relation_tenant_sql, relation_tenant_params = tenant_predicate(
        tenant_scope, alias="r", param_index=5
    )
    next_index = next_tenant_param_index(tenant_scope, 5)
    neighbor_scope_sql, neighbor_scope_params, _ = scope_predicates(
        access_scope, tenant_scope, alias="n", param_index=next_index
    )
    direction_sql = {
        "parents": "r.specific_goal_id = $1::uuid",
        "children": "r.abstract_goal_id = $1::uuid",
        "both": "(r.specific_goal_id = $1::uuid OR r.abstract_goal_id = $1::uuid)",
    }[direction]
    relation_rows = await pool.fetch(
        f"""
        SELECT r.specific_goal_id::text AS specific_goal_id,
               r.abstract_goal_id::text AS abstract_goal_id,
               r.relation_type, r.status, r.confidence, r.provenance,
               r.decision_id::text AS decision_id, r.decision_metadata,
               r.decided_by, r.decided_at,
               CASE WHEN r.specific_goal_id = $1::uuid THEN 'parent' ELSE 'child' END AS direction,
               {_PROJECTED_NEIGHBOR_COLUMNS}
        FROM goal_relations r
        JOIN ({_PROJECTED_GOAL_SOURCE}) n
          ON n.goal_id = CASE WHEN r.specific_goal_id = $1::uuid
                              THEN r.abstract_goal_id ELSE r.specific_goal_id END
        WHERE r.relation_type = 'SPECIALIZES'
          AND r.status = 'accepted'
          AND {direction_sql}
          AND r.scope_type IS NOT DISTINCT FROM $2::text
          AND r.scope_entity_id IS NOT DISTINCT FROM $3
          AND n.status IN ('active', 'candidate')
          AND n.scope_type IS NOT DISTINCT FROM $2::text
          AND n.scope_entity_id IS NOT DISTINCT FROM $3
          AND {relation_tenant_sql}
          AND {neighbor_scope_sql}
        ORDER BY n.canonical_name, n.goal_id
        LIMIT $4
        """,
        anchor_id,
        anchor_scope_type,
        anchor_scope_entity_id,
        limit,
        *relation_tenant_params,
        *neighbor_scope_params,
    )
    projected_neighbors = [
        {**dict(row), "id": row["neighbor_id"]} for row in relation_rows
    ]
    neighbor_rows, neighbor_hydration = await _hydrate_projected_goals(
        pool, projected_neighbors, pools=pools
    )
    parents: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    for relation in relation_rows:
        neighbor_id = str(relation["neighbor_id"])
        goal = neighbor_rows.get(neighbor_id)
        if goal is None:
            continue
        item = {
            "goal": _public_goal(goal),
            "relation": {
                "specific_goal_id": str(relation["specific_goal_id"]),
                "abstract_goal_id": str(relation["abstract_goal_id"]),
                "relation_type": str(relation["relation_type"]),
                "status": str(relation["status"]),
                "confidence": relation.get("confidence"),
                "provenance": relation.get("provenance"),
                "decision_id": str(relation["decision_id"]) if relation.get("decision_id") else None,
                "decision_metadata": relation.get("decision_metadata") or {},
                "decided_by": relation.get("decided_by"),
                "decided_at": relation.get("decided_at"),
            },
        }
        (children if str(relation["direction"]) == "child" else parents).append(item)
    unavailable = dict(anchor_hydration.unavailable_shards)
    unavailable.update(neighbor_hydration.unavailable_shards)
    missing_ids = list(dict.fromkeys((*anchor_hydration.missing_ids, *neighbor_hydration.missing_ids)))
    return {
        "goal": _public_goal(anchor),
        "parents": parents,
        "children": children,
        "partial": bool(unavailable),
        "unavailable_shards": unavailable,
        "missing_ids": missing_ids,
    }


def _partition_projection_relations(
    relation_rows: Sequence[Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    usable: list[dict[str, Any]] = []
    orphan_edges = 0
    cross_scope_edges = 0
    for relation in relation_rows:
        specific = str(relation["specific_goal_id"])
        abstract = str(relation["abstract_goal_id"])
        source = snapshots.get(specific)
        target = snapshots.get(abstract)
        if source is None or target is None:
            orphan_edges += 1
            continue
        source_scope = _scope_key(source.get("scope_type"), source.get("scope_entity_id"))
        target_scope = _scope_key(target.get("scope_type"), target.get("scope_entity_id"))
        relation_scope = _scope_key(
            relation.get("scope_type"), relation.get("scope_entity_id")
        )
        if source_scope != target_scope or relation_scope != source_scope:
            cross_scope_edges += 1
            continue
        usable.append(
            {
                "specific_goal_id": specific,
                "abstract_goal_id": abstract,
                "relation_type": RELATION_TYPE,
                "status": "accepted",
                "scope_type": source_scope[0],
                "scope_entity_id": source_scope[1],
            }
        )
    return usable, orphan_edges, cross_scope_edges


async def _write_abstraction_states(
    conn: Any,
    states: Sequence[Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> int:
    for state in states:
        goal = snapshots[state["goal_id"]]
        scope_type, scope_entity_id = _scope_key(
            goal.get("scope_type"), goal.get("scope_entity_id")
        )
        await conn.execute(
            """
            INSERT INTO goal_abstraction_state (
                goal_id, abstraction_level, parent_count, direct_child_count,
                coverage_total_count, coverage_resolved_count, coverage_ratio,
                direct_resolved_at, goal_status, goal_version, visibility, owner_id,
                scope_type, scope_entity_id, tenant_id, home_shard_id
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11::visibility_level, $12, $13, $14, $15::uuid, $16
            )
            ON CONFLICT (goal_id) DO UPDATE SET
                abstraction_level = EXCLUDED.abstraction_level,
                parent_count = EXCLUDED.parent_count,
                direct_child_count = EXCLUDED.direct_child_count,
                coverage_total_count = EXCLUDED.coverage_total_count,
                coverage_resolved_count = EXCLUDED.coverage_resolved_count,
                coverage_ratio = EXCLUDED.coverage_ratio,
                direct_resolved_at = EXCLUDED.direct_resolved_at,
                goal_status = EXCLUDED.goal_status,
                goal_version = EXCLUDED.goal_version,
                visibility = EXCLUDED.visibility,
                owner_id = EXCLUDED.owner_id,
                scope_type = EXCLUDED.scope_type,
                scope_entity_id = EXCLUDED.scope_entity_id,
                tenant_id = EXCLUDED.tenant_id,
                home_shard_id = EXCLUDED.home_shard_id,
                computed_at = now()
            """,
            state["goal_id"],
            state["abstraction_level"],
            state["parent_count"],
            state["direct_child_count"],
            state["coverage_total_count"],
            state["coverage_resolved_count"],
            state["coverage_ratio"],
            state["direct_resolved_at"],
            str(goal["status"]),
            int(goal.get("version") or 1),
            str(goal["visibility"]),
            goal.get("owner_id"),
            scope_type,
            scope_entity_id,
            str(goal["tenant_id"]) if goal.get("tenant_id") is not None else None,
            str(goal.get("home_shard_id") or HOME_SHARD),
        )
    return len(states)


async def recompute_affected_goal_abstraction_state(
    pool: Any,
    specific_goal_id: str | UUID,
    abstract_goal_id: str | UUID,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """Refresh projection rows for the changed edge's descendants and ancestors.

    Bounded to the edge's own component: the Goals whose state can change
    (descendants of the specific end, ancestors of the abstract end) plus what
    their state depends on (their ancestors for levels, their descendants for
    coverage). Nothing outside that closure is read or hydrated."""
    specific = _uuid(specific_goal_id, "specific_goal_id")
    abstract = _uuid(abstract_goal_id, "abstract_goal_id")
    if specific == abstract:
        raise GoalRelationSelfError("a Goal cannot specialize itself")
    closure_tenant_sql, closure_tenant_params = tenant_predicate(tenant_scope, alias="r", param_index=3)
    closure_rows = await pool.fetch(
        f"""
        WITH RECURSIVE descendants(goal_id) AS (
            SELECT $1::uuid
            UNION
            SELECT r.specific_goal_id FROM goal_relations r
            JOIN descendants d ON r.abstract_goal_id = d.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted' AND {closure_tenant_sql}
        ), ancestors(goal_id) AS (
            SELECT $2::uuid
            UNION
            SELECT r.abstract_goal_id FROM goal_relations r
            JOIN ancestors a ON r.specific_goal_id = a.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted' AND {closure_tenant_sql}
        ), seeds(goal_id) AS (
            SELECT goal_id FROM descendants UNION SELECT goal_id FROM ancestors
        ), up(goal_id) AS (
            SELECT goal_id FROM seeds
            UNION
            SELECT r.abstract_goal_id FROM goal_relations r
            JOIN up u ON r.specific_goal_id = u.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted' AND {closure_tenant_sql}
        ), down(goal_id) AS (
            SELECT goal_id FROM seeds
            UNION
            SELECT r.specific_goal_id FROM goal_relations r
            JOIN down d ON r.abstract_goal_id = d.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted' AND {closure_tenant_sql}
        )
        SELECT goal_id::text AS goal_id FROM up
        UNION
        SELECT goal_id::text FROM down
        """,
        specific,
        abstract,
        *closure_tenant_params,
    )
    closure_ids = sorted({str(row["goal_id"]) for row in closure_rows} | {specific, abstract})
    scope_sql, scope_params, _ = scope_predicates(
        access_scope, tenant_scope, alias="g", param_index=2
    )
    projected_rows = await pool.fetch(
        _projected_goal_rows(
            f"g.goal_id = ANY($1::uuid[]) AND g.status IN ('active', 'candidate') AND {scope_sql}"
        ),
        closure_ids,
        *scope_params,
    )
    snapshots, hydration = await _hydrate_projected_goals(pool, projected_rows, pools=pools)
    specific_goal = snapshots.get(specific)
    abstract_goal = snapshots.get(abstract)
    if hydration.partial or specific_goal is None or abstract_goal is None:
        return {
            "affected_goals": 0,
            "goals_recomputed": 0,
            "accepted_edges": 0,
            "usable_edges": 0,
            "orphan_edges": 0,
            "cross_scope_edges": 0,
            "component_goals": len(closure_ids),
            "unavailable_shards": dict(hydration.unavailable_shards),
            "missing_ids": list(hydration.missing_ids),
            "partial": True,
            "recomputed": False,
        }
    scope_type, scope_entity_id = _scope_key(
        specific_goal.get("scope_type"), specific_goal.get("scope_entity_id")
    )
    relation_scope_sql, relation_scope_params = tenant_predicate(
        tenant_scope, alias="r", param_index=5
    )
    async with tenant_transaction(pool, tenant_scope) as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"goal-abstraction-state:{tenant_scope.tenant_id or 'unrestricted'}",
        )
        affected_rows = await conn.fetch(
            f"""
            WITH RECURSIVE descendants(goal_id) AS (
                SELECT $1::uuid
                UNION
                SELECT r.specific_goal_id
                FROM goal_relations r
                JOIN descendants d ON r.abstract_goal_id = d.goal_id
                WHERE r.relation_type = 'SPECIALIZES'
                  AND r.status = 'accepted'
                  AND r.scope_type IS NOT DISTINCT FROM $3::text
                  AND r.scope_entity_id IS NOT DISTINCT FROM $4
                  AND {relation_scope_sql}
            ), ancestors(goal_id) AS (
                SELECT $2::uuid
                UNION
                SELECT r.abstract_goal_id
                FROM goal_relations r
                JOIN ancestors a ON r.specific_goal_id = a.goal_id
                WHERE r.relation_type = 'SPECIALIZES'
                  AND r.status = 'accepted'
                  AND r.scope_type IS NOT DISTINCT FROM $3::text
                  AND r.scope_entity_id IS NOT DISTINCT FROM $4
                  AND {relation_scope_sql}
            )
            SELECT goal_id FROM descendants
            UNION
            SELECT goal_id FROM ancestors
            """,
            specific,
            abstract,
            scope_type,
            scope_entity_id,
            *relation_scope_params,
        )
        affected = {
            str(row["goal_id"])
            for row in affected_rows
            if str(row["goal_id"]) in snapshots
        }
        component_relation_sql, component_relation_params = tenant_predicate(
            tenant_scope, alias="r", param_index=2
        )
        relation_rows = await conn.fetch(
            f"""
            SELECT specific_goal_id::text AS specific_goal_id,
                   abstract_goal_id::text AS abstract_goal_id,
                   scope_type, scope_entity_id
            FROM goal_relations r
            WHERE r.relation_type = 'SPECIALIZES'
              AND r.status = 'accepted'
              AND r.specific_goal_id = ANY($1::uuid[])
              AND r.abstract_goal_id = ANY($1::uuid[])
              AND {component_relation_sql}
            """,
            closure_ids,
            *component_relation_params,
        )
        usable_relations, orphan_edges, cross_scope_edges = _partition_projection_relations(
            relation_rows, snapshots
        )
        derived = derive_goal_abstraction_state(
            snapshots,
            usable_relations,
            direct_resolved_at={
                goal_id: goal.get("resolved_at") for goal_id, goal in snapshots.items()
            },
        )
        recomputed = [state for state in derived if state["goal_id"] in affected]
        goals_recomputed = await _write_abstraction_states(conn, recomputed, snapshots)
    return {
        "affected_goals": len(affected),
        "goals_recomputed": goals_recomputed,
        "accepted_edges": len(relation_rows),
        "usable_edges": len(usable_relations),
        "orphan_edges": orphan_edges,
        "cross_scope_edges": cross_scope_edges,
        "component_goals": len(closure_ids),
        "unavailable_shards": dict(hydration.unavailable_shards),
        "missing_ids": list(hydration.missing_ids),
        "partial": False,
        "recomputed": True,
    }


async def refresh_goal_abstraction_state_after_resolution(
    pool: Any, goal_id: str | UUID, *, pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """`goals.resolved_at` changed for `goal_id`: its own `direct_resolved_at` and
    every ancestor's coverage are now stale in `goal_abstraction_state`. Refresh
    exactly that part of the component (one bounded recompute per accepted parent
    edge; a Goal with only children refreshes itself through one child edge).

    This only updates the DERIVED projection; nothing here resolves any Goal:
    resolution never propagates through the hierarchy."""
    goal = _uuid(goal_id, "goal_id")
    rows = await pool.fetch(
        """
        SELECT specific_goal_id::text AS specific, abstract_goal_id::text AS abstract,
               (specific_goal_id = $1::uuid) AS is_parent_edge
          FROM goal_relations
         WHERE relation_type = 'SPECIALIZES' AND status = 'accepted'
           AND (specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid)
         ORDER BY is_parent_edge DESC, specific_goal_id, abstract_goal_id
        """,
        goal,
    )
    parent_edges = [row for row in rows if row["is_parent_edge"]]
    edges = parent_edges or rows[:1]
    if not edges:
        return {"recomputed": False, "reason": "no_accepted_edges", "edges": 0}
    results = [
        await recompute_affected_goal_abstraction_state(
            pool, row["specific"], row["abstract"], access_scope=AccessScope.unrestricted(),
            tenant_scope=TenantScope.unrestricted(), pools=pools,
        )
        for row in edges
    ]
    return {
        "recomputed": all(result.get("recomputed") for result in results),
        "edges": len(edges),
        "goals_recomputed": sum(int(result.get("goals_recomputed") or 0) for result in results),
    }


async def rebuild_goal_abstraction_state(
    pool: Any,
    *,
    access_scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: TenantScope = TenantScope.unrestricted(),
    pools: Optional[ShardPools] = None,
) -> dict[str, Any]:
    """Recompute the scalar projection from accepted canonical relations."""
    scope_sql, scope_params, _ = scope_predicates(
        access_scope, tenant_scope, alias="g", param_index=1
    )
    projected_rows = await pool.fetch(
        _projected_goal_rows(
            f"g.status IN ('active', 'candidate') AND {scope_sql}"
        ),
        *scope_params,
    )
    snapshots, hydration = await _hydrate_projected_goals(pool, projected_rows, pools=pools)
    if hydration.partial:
        return {
            "goals_projected": 0,
            "accepted_edges": 0,
            "usable_edges": 0,
            "orphan_edges": 0,
            "cross_scope_edges": 0,
            "orphans_deleted": 0,
            "unavailable_shards": dict(hydration.unavailable_shards),
            "missing_ids": list(hydration.missing_ids),
            "partial": True,
            "rebuilt": False,
        }
    relation_scope_sql, relation_scope_params = tenant_predicate(
        tenant_scope, alias="r", param_index=1
    )
    async with tenant_transaction(pool, tenant_scope) as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"goal-abstraction-state:{tenant_scope.tenant_id or 'unrestricted'}",
        )
        relation_rows = await conn.fetch(
            f"""
            SELECT specific_goal_id::text AS specific_goal_id,
                   abstract_goal_id::text AS abstract_goal_id,
                   scope_type, scope_entity_id
            FROM goal_relations r
            WHERE r.relation_type = 'SPECIALIZES'
              AND r.status = 'accepted'
              AND {relation_scope_sql}
            """,
            *relation_scope_params,
        )
        usable_relations, orphan_edges, cross_scope_edges = _partition_projection_relations(
            relation_rows, snapshots
        )
        derived = derive_goal_abstraction_state(
            snapshots,
            usable_relations,
            direct_resolved_at={
                goal_id: goal.get("resolved_at") for goal_id, goal in snapshots.items()
            },
        )
        await _write_abstraction_states(conn, derived, snapshots)
        delete_scope_sql, delete_scope_params, _ = scope_predicates(
            access_scope, tenant_scope, alias="s", param_index=2
        )
        deleted = await conn.execute(
            f"""
            DELETE FROM goal_abstraction_state s
            WHERE {delete_scope_sql}
              AND NOT (s.goal_id = ANY($1::uuid[]))
            """,
            list(snapshots),
            *delete_scope_params,
        )
    orphan_count = int(str(deleted).rsplit(" ", 1)[-1])
    return {
        "goals_projected": len(snapshots),
        "accepted_edges": len(relation_rows),
        "usable_edges": len(usable_relations),
        "orphan_edges": orphan_edges,
        "cross_scope_edges": cross_scope_edges,
        "orphans_deleted": orphan_count,
        "unavailable_shards": dict(hydration.unavailable_shards),
        "missing_ids": list(hydration.missing_ids),
        "partial": False,
        "rebuilt": True,
    }


async def get_goal_abstraction_state(
    pool: Any,
    goal_id: str | UUID,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    pools: Optional[ShardPools] = None,
) -> Optional[dict[str, Any]]:
    """Read one viewer-scoped derived state row after rechecking its Goal."""
    normalized = _uuid(goal_id, "goal_id")
    scope_sql, scope_params, _ = scope_predicates(
        access_scope, tenant_scope, alias="s", param_index=2
    )
    row = await pool.fetchrow(
        f"""
        SELECT s.goal_id::text AS goal_id, s.abstraction_level, s.parent_count,
               s.direct_child_count, s.coverage_total_count, s.coverage_resolved_count,
               s.coverage_ratio, s.direct_resolved_at, s.goal_status, s.goal_version,
               s.visibility::text AS visibility, s.owner_id, s.scope_type,
               s.scope_entity_id, s.tenant_id::text AS tenant_id, s.home_shard_id,
               s.computed_at
        FROM goal_abstraction_state s
        WHERE s.goal_id = $1::uuid AND {scope_sql}
        """,
        normalized,
        *scope_params,
    )
    if row is None:
        return None
    snapshots, hydration = await _visible_goal_snapshots(
        pool,
        (normalized,),
        access_scope=access_scope,
        tenant_scope=tenant_scope,
        pools=pools,
    )
    snapshot = snapshots.get(normalized)
    if hydration.partial or snapshot is None:
        return None
    if (
        str(row["goal_status"]) != str(snapshot.get("status"))
        or int(row["goal_version"]) != int(snapshot.get("version") or 0)
        or str(row["visibility"]) != str(snapshot.get("visibility"))
        or row.get("owner_id") != snapshot.get("owner_id")
        or _scope_key(row.get("scope_type"), row.get("scope_entity_id"))
        != _scope_key(snapshot.get("scope_type"), snapshot.get("scope_entity_id"))
    ):
        return None
    return dict(row)
