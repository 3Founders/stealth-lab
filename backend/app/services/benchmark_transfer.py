"""Asynchronous, idempotent transfer of benchmark definitions across direct accepted Goal neighbors."""
from __future__ import annotations

import inspect
import json
import math
from contextlib import AsyncExitStack
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, Optional

from app.execution.plans import canonical_json, sha256_hex
from app.ingestion import queue as ingestion_queue
from app.services.access import (
    AccessScope,
    TenantScope,
    tenant_predicate,
    tenant_transaction,
)
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.shards import HOME_SHARD, home_pool
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7

JOB_TYPE = "benchmark_transfer"
TRANSFER_PROVENANCE = "system_pending_review"
TRANSFER_VERSION = "benchmark_transfer@v1"
TRANSFER_DECISIONS = ("transferable", "partial", "uncertain", "not_transferable")
SPECIFIC_TO_ABSTRACT = "specific_to_abstract"
ABSTRACT_TO_SPECIFIC = "abstract_to_specific"
SOURCE_TO_TARGET = "source_to_target"
TRANSFER_ACTOR = "benchmark_transfer"
COMMONS_TENANT_ID = "00000000-0000-0000-0000-000000000001"


class BenchmarkTransferError(ValueError):
    pass


class BenchmarkSourceInvalid(BenchmarkTransferError):
    pass


class BenchmarkTransferPrivacyError(BenchmarkTransferError):
    pass


class BenchmarkTransferStale(RuntimeError):
    pass


@dataclass(frozen=True)
class TransferDecision:
    decision: str
    confidence: float
    reason: str
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "reason": self.reason,
            "provenance": dict(self.provenance),
        }


def _row_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError) as exc:
        raise BenchmarkTransferError("database row is not a mapping") from exc


def _first(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def _required_id(value: Any, field: str) -> str:
    if value is None or not str(value).strip():
        raise BenchmarkSourceInvalid(f"{field} is required")
    return str(value).strip()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkSourceInvalid(f"{field} is required")
    return value.strip()


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_value(value: Any, field: str, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (Mapping, list, tuple)):
        return list(value) if isinstance(value, tuple) else value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise BenchmarkSourceInvalid(f"{field} must be valid JSON") from exc
    raise BenchmarkSourceInvalid(f"{field} must be JSON")


def _json_mapping(value: Any, field: str, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    parsed = _json_value(value, field, {} if default is None else default)
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise BenchmarkSourceInvalid(f"{field} must be a JSON object")
    return dict(parsed)


def _json_list(value: Any, field: str) -> list[Any]:
    parsed = _json_value(value, field, [])
    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise BenchmarkSourceInvalid(f"{field} must be a JSON array")
    return list(parsed)


def _json_text(value: Any) -> str:
    return canonical_json(value)


def _scope_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    scope_type = str(_first(row, "scope_type", default="") or "").strip().lower()
    scope_entity_id = _first(row, "scope_entity_id", default=None)
    if scope_type == "global":
        scope_entity_id = None
    scope_type, scope_entity_id = validate_scope(scope_type, scope_entity_id)
    visibility = str(_first(row, "visibility", default="public") or "public").strip().lower()
    if visibility not in ("public", "private"):
        raise BenchmarkSourceInvalid("org benchmark transfer is unsupported in v1")
    owner_id = _optional_text(_first(row, "owner_id", default=None))
    if visibility == "private" and not owner_id:
        raise BenchmarkSourceInvalid("private scope requires owner_id")
    return {
        "scope_type": scope_type,
        "scope_entity_id": scope_entity_id,
        "visibility": visibility,
        "owner_id": owner_id,
    }


def scope_signature(row: Mapping[str, Any]) -> dict[str, Any]:
    return _scope_from_row(row)


def _query_access_scope(access_scope: AccessScope) -> AccessScope:
    return access_scope


def _scope_visible(scope: Mapping[str, Any], access_scope: AccessScope) -> bool:
    if access_scope.is_unrestricted:
        return True
    if scope["visibility"] == "public":
        return True
    if not access_scope.include_private:
        return False
    if access_scope.viewer_id is None:
        return False
    if scope["owner_id"] == access_scope.viewer_id:
        return True
    return scope["visibility"] == "org" and scope["scope_entity_id"] in access_scope.org_ids


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
    left_scope = _scope_from_row(left)
    right_scope = _scope_from_row(right)
    for field in ("scope_type", "scope_entity_id", "visibility"):
        if left_scope[field] != right_scope[field]:
            return False
    if left_scope["visibility"] == "private" and left_scope["owner_id"] != right_scope["owner_id"]:
        return False
    return True


def _require_same_scope(source: Mapping[str, Any], target: Mapping[str, Any]) -> None:
    left = source.get("scope", source)
    right = target.get("scope", target)
    if not _same_scope(left, right):
        raise BenchmarkTransferPrivacyError("benchmark transfer requires identical source and target privacy scope")


def _normalise_source(row: Any) -> dict[str, Any]:
    raw = _row_dict(row)
    if raw is None:
        raise BenchmarkSourceInvalid("source benchmark not found or not visible")
    benchmark_id = _required_id(_first(raw, "benchmark_id", "id"), "benchmark_id")
    source_goal_id = _required_id(_first(raw, "source_goal_id", "goal_id"), "source_goal_id")
    joined_goal_id = _first(raw, "joined_goal_id", "goal_id", default=None)
    if joined_goal_id is not None and str(joined_goal_id) != source_goal_id:
        raise BenchmarkSourceInvalid("source benchmark goal does not match its Goal row")
    name = _required_text(_first(raw, "benchmark_name", "name"), "benchmark name")
    version_value = _first(raw, "benchmark_version", "version", default=1)
    if isinstance(version_value, bool):
        raise BenchmarkSourceInvalid("benchmark version must be a positive integer")
    try:
        version = int(version_value)
    except (TypeError, ValueError) as exc:
        raise BenchmarkSourceInvalid("benchmark version must be a positive integer") from exc
    if version < 1:
        raise BenchmarkSourceInvalid("benchmark version must be a positive integer")
    benchmark_status = str(_first(raw, "benchmark_status", "status", default="draft") or "draft").strip().lower()
    if benchmark_status != "frozen":
        raise BenchmarkSourceInvalid("source benchmark must have frozen status")
    frozen_at = _first(raw, "benchmark_frozen_at", "frozen_at", default=None)
    if frozen_at is None:
        raise BenchmarkSourceInvalid("source benchmark must have non-null frozen_at")
    goal_status = str(_first(raw, "goal_status", default="active") or "active").strip().lower()
    if goal_status == "merged" or bool(_first(raw, "goal_t_invalid", "t_invalid", default=False)):
        raise BenchmarkSourceInvalid("source Goal is not live")
    goal_name = _required_text(_first(raw, "goal_name", "canonical_name"), "source Goal name")
    metadata = _json_mapping(_first(raw, "benchmark_metadata", "metadata", default={}), "benchmark metadata")
    invariants = metadata.get("invariants", [])
    if not isinstance(invariants, list):
        raise BenchmarkSourceInvalid("benchmark metadata invariants must be a JSON array")
    scope = _scope_from_row({
        "scope_type": _first(raw, "goal_scope_type", "scope_type"),
        "scope_entity_id": _first(raw, "goal_scope_entity_id", "scope_entity_id"),
        "visibility": _first(raw, "goal_visibility", "visibility"),
        "owner_id": _first(raw, "goal_owner_id", "owner_id"),
    })
    result = {
        "benchmark_id": benchmark_id,
        "goal_id": source_goal_id,
        "name": name,
        "description": _optional_text(_first(raw, "benchmark_description", "description", default=None)),
        "version": version,
        "evaluation_protocol": _json_mapping(
            _first(raw, "benchmark_evaluation_protocol", "evaluation_protocol", default={}),
            "evaluation_protocol",
        ),
        "environment_specification": _json_mapping(
            _first(raw, "benchmark_environment_specification", "environment_specification", default={}),
            "environment_specification",
        ),
        "success_criteria": _json_mapping(
            _first(raw, "benchmark_success_criteria", "success_criteria", default={}),
            "success_criteria",
        ),
        "comparison_policy": _json_mapping(
            _first(raw, "benchmark_comparison_policy", "comparison_policy", default={}),
            "comparison_policy",
        ),
        "invariants": list(invariants),
        "status": benchmark_status,
        "frozen_at": frozen_at,
        "provenance": _optional_text(_first(raw, "benchmark_provenance", "provenance", default=None)),
        "goal_name": goal_name,
        "goal_description": _optional_text(_first(raw, "goal_description", default=None)),
        "goal_objective": _optional_text(_first(raw, "goal_objective", "objective", default=None)),
        "goal_constraints": _json_value(
            _first(raw, "goal_constraints", "constraints", default=[]), "goal constraints", []
        ),
        "goal_expected_outcome": _json_value(
            _first(raw, "goal_expected_outcome", "expected_outcome", default={}),
            "goal expected outcome",
            {},
        ),
        "goal_verification_requirement": _json_value(
            _first(raw, "goal_verification_requirement", "verification_requirement", default={}),
            "goal verification requirement",
            {},
        ),
        "goal_version": _first(raw, "goal_version", default=1),
        "goal_status": goal_status,
        "home_shard_id": _optional_text(_first(raw, "home_shard_id", "goal_home_shard_id", default=None)),
        "tenant_id": _optional_text(_first(raw, "goal_tenant_id", "tenant_id", default=None)),
        "scope": scope,
    }
    result["source_fingerprint"] = benchmark_fingerprint(result)
    return result


def validate_source_structure(row: Any) -> dict[str, Any]:
    return _normalise_source(row)


validate_benchmark_source = validate_source_structure
validate_transfer_source = validate_source_structure


def _normalise_target(row: Any) -> dict[str, Any]:
    raw = _row_dict(row)
    if raw is None:
        raise BenchmarkTransferError("target Goal not found or not visible")
    target_id = _required_id(_first(raw, "target_goal_id", "id"), "target Goal id")
    status = str(_first(raw, "goal_status", "status", default="active") or "active").strip().lower()
    if status == "merged" or bool(_first(raw, "goal_t_invalid", "t_invalid", default=False)):
        raise BenchmarkTransferError("target Goal is not live")
    name = _required_text(_first(raw, "target_name", "goal_name", "canonical_name"), "target Goal name")
    scope = _scope_from_row({
        "scope_type": _first(raw, "target_scope_type", "goal_scope_type", "scope_type"),
        "scope_entity_id": _first(raw, "target_scope_entity_id", "goal_scope_entity_id", "scope_entity_id"),
        "visibility": _first(raw, "target_visibility", "goal_visibility", "visibility"),
        "owner_id": _first(raw, "target_owner_id", "goal_owner_id", "owner_id"),
    })
    target = {
        "goal_id": target_id,
        "name": name,
        "description": _optional_text(_first(raw, "target_description", "goal_description", "description", default=None)),
        "objective": _optional_text(_first(raw, "target_objective", "goal_objective", "objective", default=None)),
        "constraints": _json_value(
            _first(raw, "target_constraints", "goal_constraints", "constraints", default=[]),
            "target constraints",
            [],
        ),
        "expected_outcome": _json_value(
            _first(raw, "target_expected_outcome", "goal_expected_outcome", "expected_outcome", default={}),
            "target expected outcome",
            {},
        ),
        "verification_requirement": _json_value(
            _first(raw, "target_verification_requirement", "goal_verification_requirement", "verification_requirement", default={}),
            "target verification requirement",
            {},
        ),
        "version": _first(raw, "target_version", "goal_version", default=1),
        "status": status,
        "home_shard_id": _optional_text(_first(raw, "target_home_shard_id", "home_shard_id", default=None)),
        "scope": scope,
    }
    target["target_fingerprint"] = goal_fingerprint(target)
    return target


def benchmark_fingerprint(source: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json({
        "benchmark_id": str(source.get("benchmark_id", "")),
        "goal_id": str(source.get("goal_id", "")),
        "name": source.get("name"),
        "description": source.get("description"),
        "version": source.get("version"),
        "evaluation_protocol": source.get("evaluation_protocol", {}),
        "environment_specification": source.get("environment_specification", {}),
        "success_criteria": source.get("success_criteria", {}),
        "comparison_policy": source.get("comparison_policy", {}),
        "invariants": source.get("invariants", []),
        "status": source.get("status"),
        "frozen_at": str(source.get("frozen_at")) if source.get("frozen_at") is not None else None,
        "provenance": source.get("provenance"),
        "source_goal": {
            "name": source.get("goal_name"),
            "description": source.get("goal_description"),
            "objective": source.get("goal_objective"),
            "constraints": source.get("goal_constraints", []),
            "expected_outcome": source.get("goal_expected_outcome", {}),
            "verification_requirement": source.get("goal_verification_requirement", {}),
            "version": source.get("goal_version"),
            "status": source.get("goal_status"),
            "scope": source.get("scope", {}),
            "tenant_id": source.get("tenant_id"),
        },
    }))


def goal_fingerprint(target: Mapping[str, Any]) -> str:
    scope = target.get("scope", target)
    return sha256_hex(canonical_json({
        "goal_id": str(target.get("goal_id", "")),
        "name": target.get("name"),
        "description": target.get("description"),
        "objective": target.get("objective"),
        "constraints": target.get("constraints", []),
        "expected_outcome": target.get("expected_outcome", {}),
        "verification_requirement": target.get("verification_requirement", {}),
        "version": target.get("version", 1),
        "status": target.get("status"),
        "scope": {
            "scope_type": scope.get("scope_type"),
            "scope_entity_id": scope.get("scope_entity_id"),
            "visibility": scope.get("visibility"),
            "owner_id": scope.get("owner_id"),
            "tenant_id": target.get("tenant_id"),
        },
    }))


def benchmark_transfer_idempotency_key(
    source_benchmark_id: str,
    target_goal_id: str,
    *,
    source_fingerprint: str = "",
    target_fingerprint: str = "",
    relation_fingerprint: str = "",
    direction: str = SOURCE_TO_TARGET,
    policy_version: str = TRANSFER_VERSION,
) -> str:
    material = {
        "version": 2,
        "source_benchmark_id": str(source_benchmark_id).strip(),
        "target_goal_id": str(target_goal_id).strip(),
        "source_fingerprint": str(source_fingerprint or ""),
        "target_fingerprint": str(target_fingerprint or ""),
        "relation_fingerprint": str(relation_fingerprint or ""),
        "direction": str(direction or SOURCE_TO_TARGET),
        "policy_version": str(policy_version or ""),
    }
    return "benchmark-transfer:" + sha256_hex(canonical_json(material))


stable_transfer_key = benchmark_transfer_idempotency_key
benchmark_transfer_key = benchmark_transfer_idempotency_key
transfer_idempotency_key = benchmark_transfer_idempotency_key


async def _visible_goal_snapshot(
    pool: Any,
    goal_id: str,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
) -> Optional[dict[str, Any]]:
    scope_sql, scope_params = _goal_visibility_predicate(
        _query_access_scope(access_scope or AccessScope.anonymous()),
        alias="g",
        param_index=2,
    )
    projection = await pool.fetchrow(
        f"""
        SELECT g.goal_id::text AS id, g.canonical_name, g.status AS projected_status,
               g.version AS projected_version, g.scope_type AS projected_scope_type,
               g.scope_entity_id AS projected_scope_entity_id,
               g.visibility::text AS projected_visibility,
               g.owner_id AS projected_owner_id, g.home_shard_id
        FROM goal_search_index g
        WHERE g.goal_id = $1::uuid AND g.status IN ('active', 'candidate') AND {scope_sql}
        """,
        goal_id,
        *scope_params,
    )
    if projection is None:
        return None
    owner = await home_pool(pool, "goal", goal_id)
    canonical = await owner.fetchrow(
        """
        SELECT id::text AS id, canonical_name, description, objective, constraints,
               expected_outcome, verification_requirement, version, status, t_invalid,
               scope_type, scope_entity_id, visibility::text AS visibility,
               owner_id, home_shard_id
        FROM goals
        WHERE id = $1::uuid AND t_invalid IS NULL
        """,
        goal_id,
    )
    if canonical is None:
        return None
    if (
        str(canonical["status"]) not in ("active", "candidate")
        or str(canonical["status"]) != str(projection["projected_status"])
        or int(canonical.get("version") or 0) != int(projection.get("projected_version") or 0)
        or _scope_key_for_goal(canonical) != (
            str(projection.get("projected_scope_type") or "global"),
            None
            if str(projection.get("projected_scope_type") or "global") == "global"
            else str(projection.get("projected_scope_entity_id") or "") or None,
        )
        or str(canonical["visibility"]) != str(projection["projected_visibility"])
        or canonical.get("owner_id") != projection.get("projected_owner_id")
    ):
        return None
    return {
        **dict(canonical),
        "tenant_id": str(tenant_scope.tenant_id) if tenant_scope.tenant_id is not None else None,
        "home_shard_id": str(projection.get("home_shard_id") or HOME_SHARD),
    }


def _scope_key_for_goal(row: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    scope_type = str(row.get("scope_type") or "global")
    return scope_type, None if scope_type == "global" else str(row.get("scope_entity_id") or "") or None


def _source_from_rows(
    benchmark: Mapping[str, Any], goal: Mapping[str, Any]
) -> dict[str, Any]:
    raw = {
        **dict(benchmark),
        "joined_goal_id": goal["id"],
        "goal_name": goal["canonical_name"],
        "goal_description": goal.get("description"),
        "goal_objective": goal.get("objective"),
        "goal_constraints": goal.get("constraints", []),
        "goal_expected_outcome": goal.get("expected_outcome", {}),
        "goal_verification_requirement": goal.get("verification_requirement", {}),
        "goal_version": goal.get("version"),
        "goal_status": goal.get("status"),
        "goal_t_invalid": goal.get("t_invalid"),
        "goal_scope_type": goal.get("scope_type"),
        "goal_scope_entity_id": goal.get("scope_entity_id"),
        "goal_visibility": goal.get("visibility"),
        "goal_owner_id": goal.get("owner_id"),
        "goal_home_shard_id": goal.get("home_shard_id"),
        "goal_tenant_id": goal.get("tenant_id"),
    }
    source = _normalise_source(raw)
    source["tenant_id"] = goal["tenant_id"]
    source["source_fingerprint"] = benchmark_fingerprint(source)
    return source


async def get_benchmark_transfer_source(
    pool: Any,
    source_benchmark_id: str,
    *,
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    source_id = _required_id(source_benchmark_id, "source_benchmark_id")
    effective_tenant = tenant_scope or TenantScope.commons()
    benchmark = await pool.fetchrow(
        """
        SELECT id AS benchmark_id, goal_id AS source_goal_id, name AS benchmark_name,
               description AS benchmark_description, version AS benchmark_version,
               evaluation_protocol AS benchmark_evaluation_protocol,
               environment_specification AS benchmark_environment_specification,
               success_criteria AS benchmark_success_criteria,
               comparison_policy AS benchmark_comparison_policy,
               status AS benchmark_status, frozen_at AS benchmark_frozen_at,
               provenance AS benchmark_provenance, metadata AS benchmark_metadata
        FROM benchmarks b
        WHERE b.id = $1::uuid
        """,
        source_id,
    )
    if benchmark is None:
        raise BenchmarkSourceInvalid("source benchmark not found or not visible")
    source_goal_id = _required_id(_first(dict(benchmark), "source_goal_id"), "source_goal_id")
    goal = await _visible_goal_snapshot(
        pool,
        source_goal_id,
        access_scope=access_scope,
        tenant_scope=effective_tenant,
    )
    if goal is None:
        raise BenchmarkSourceInvalid("source benchmark not found or not visible")
    source = _source_from_rows(dict(benchmark), goal)
    if not _scope_visible(source["scope"], access_scope or AccessScope.anonymous()):
        raise BenchmarkSourceInvalid("source benchmark not found or not visible")
    return source


def _target_from_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    target = _normalise_target({
        "target_goal_id": goal["id"],
        "target_name": goal["canonical_name"],
        "target_description": goal.get("description"),
        "target_objective": goal.get("objective"),
        "target_constraints": goal.get("constraints", []),
        "target_expected_outcome": goal.get("expected_outcome", {}),
        "target_verification_requirement": goal.get("verification_requirement", {}),
        "target_version": goal.get("version"),
        "goal_status": goal.get("status"),
        "goal_t_invalid": goal.get("t_invalid"),
        "target_scope_type": goal.get("scope_type"),
        "target_scope_entity_id": goal.get("scope_entity_id"),
        "target_visibility": goal.get("visibility"),
        "target_owner_id": goal.get("owner_id"),
        "target_home_shard_id": goal.get("home_shard_id"),
    })
    target["tenant_id"] = goal["tenant_id"]
    target["target_fingerprint"] = goal_fingerprint(target)
    return target


async def get_benchmark_transfer_target(
    pool: Any,
    target_goal_id: str,
    *,
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    target_id = _required_id(target_goal_id, "target Goal id")
    goal = await _visible_goal_snapshot(
        pool,
        target_id,
        access_scope=access_scope,
        tenant_scope=tenant_scope or TenantScope.commons(),
    )
    if goal is None:
        raise BenchmarkTransferError("target Goal not found or not visible")
    target = _target_from_goal(goal)
    if not _scope_visible(target["scope"], access_scope or AccessScope.anonymous()):
        raise BenchmarkTransferError("target Goal not found or not visible")
    return target



def relation_fingerprint(relation: Mapping[str, Any]) -> str:
    decided_at = relation.get("decided_at")
    updated_at = relation.get("updated_at")
    metadata = relation.get("decision_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    return sha256_hex(canonical_json({
        "specific_goal_id": str(relation.get("specific_goal_id") or ""),
        "abstract_goal_id": str(relation.get("abstract_goal_id") or ""),
        "relation_type": str(relation.get("relation_type") or ""),
        "status": str(relation.get("status") or ""),
        "confidence": relation.get("confidence"),
        "provenance": relation.get("provenance"),
        "decision_id": str(relation.get("decision_id")) if relation.get("decision_id") else None,
        "decision_metadata": metadata,
        "decided_by": relation.get("decided_by"),
        "decided_at": str(decided_at) if decided_at is not None else None,
        "updated_at": str(updated_at) if updated_at is not None else None,
        "scope_type": relation.get("scope_type"),
        "scope_entity_id": relation.get("scope_entity_id"),
        "tenant_id": str(relation.get("tenant_id")) if relation.get("tenant_id") else None,
    }))


async def _load_relation(
    pool: Any,
    source_goal_id: str,
    target_goal_id: str,
    *,
    tenant_scope: TenantScope,
) -> dict[str, Any]:
    tenant_sql, tenant_params = tenant_predicate(tenant_scope, alias="r", param_index=3)
    row = await pool.fetchrow(
        f"""
        SELECT r.specific_goal_id::text AS specific_goal_id,
               r.abstract_goal_id::text AS abstract_goal_id, r.relation_type, r.status,
               r.confidence, r.provenance, r.decision_id::text AS decision_id,
               r.decision_metadata, r.decided_by, r.decided_at, r.updated_at,
               r.scope_type, r.scope_entity_id, r.tenant_id::text AS tenant_id
        FROM goal_relations r
        WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
          AND ((r.specific_goal_id = $1 AND r.abstract_goal_id = $2)
            OR (r.specific_goal_id = $2 AND r.abstract_goal_id = $1))
          AND {tenant_sql}
        LIMIT 1
        """,
        source_goal_id,
        target_goal_id,
        *tenant_params,
    )
    raw = _row_dict(row)
    if raw is None:
        raise BenchmarkTransferError("source and target Goals are not direct accepted neighbors")
    specific = _required_id(_first(raw, "specific_goal_id"), "specific_goal_id")
    abstract = _required_id(_first(raw, "abstract_goal_id"), "abstract_goal_id")
    if specific == source_goal_id and abstract == target_goal_id:
        direction = SPECIFIC_TO_ABSTRACT
    elif specific == target_goal_id and abstract == source_goal_id:
        direction = ABSTRACT_TO_SPECIFIC
    else:
        raise BenchmarkTransferError("accepted Goal relation endpoints do not match the transfer")
    relation = {
        "specific_goal_id": specific,
        "abstract_goal_id": abstract,
        "relation_type": "SPECIALIZES",
        "status": "accepted",
        "confidence": _first(raw, "confidence", default=None),
        "provenance": _optional_text(_first(raw, "provenance", default=None)),
        "decision_id": _optional_text(_first(raw, "decision_id", default=None)),
        "decision_metadata": _json_mapping(
            _first(raw, "decision_metadata", default={}), "relation decision metadata"
        ),
        "decided_by": _optional_text(_first(raw, "decided_by", default=None)),
        "decided_at": _first(raw, "decided_at", default=None),
        "updated_at": _first(raw, "updated_at", default=None),
        "scope_type": _first(raw, "scope_type", default=None),
        "scope_entity_id": _first(raw, "scope_entity_id", default=None),
        "tenant_id": _optional_text(_first(raw, "tenant_id", default=None)),
        "direction": direction,
    }
    relation["relation_fingerprint"] = relation_fingerprint(relation)
    return relation


async def _load_context(
    pool: Any,
    source_benchmark_id: str,
    target_goal_id: str,
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
) -> dict[str, Any]:
    source = await get_benchmark_transfer_source(
        pool, source_benchmark_id, access_scope=access_scope, tenant_scope=tenant_scope
    )
    target = await get_benchmark_transfer_target(
        pool, target_goal_id, access_scope=access_scope, tenant_scope=tenant_scope
    )
    _require_same_scope(source["scope"], target["scope"])
    if str(source.get("tenant_id")) != str(target.get("tenant_id")):
        raise BenchmarkTransferPrivacyError("benchmark transfer requires identical source and target tenants")
    relation = await _load_relation(
        pool, source["goal_id"], target["goal_id"], tenant_scope=tenant_scope
    )
    key = benchmark_transfer_idempotency_key(
        source["benchmark_id"],
        target["goal_id"],
        source_fingerprint=source["source_fingerprint"],
        target_fingerprint=target["target_fingerprint"],
        relation_fingerprint=relation["relation_fingerprint"],
        direction=relation["direction"],
        policy_version=TRANSFER_VERSION,
    )
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "direction": relation["direction"],
        "idempotency_key": key,
    }


async def _load_direct_neighbors(
    pool: Any,
    source: Mapping[str, Any],
    *,
    access_scope: AccessScope,
    tenant_scope: TenantScope,
) -> list[dict[str, Any]]:
    relation_scope_sql, relation_scope_params = tenant_predicate(
        tenant_scope, alias="r", param_index=2
    )
    rows = await pool.fetch(
        f"""
        SELECT r.specific_goal_id::text AS specific_goal_id,
               r.abstract_goal_id::text AS abstract_goal_id,
               r.relation_type, r.status, r.confidence, r.provenance,
               r.decision_id::text AS decision_id, r.decision_metadata,
               r.decided_by, r.decided_at, r.updated_at, r.scope_type,
               r.scope_entity_id, r.tenant_id::text AS tenant_id,
               CASE WHEN r.specific_goal_id = $1::uuid
                    THEN r.abstract_goal_id ELSE r.specific_goal_id END AS target_goal_id
        FROM goal_relations r
        WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
          AND (r.specific_goal_id = $1::uuid OR r.abstract_goal_id = $1::uuid)
          AND {relation_scope_sql}
        ORDER BY target_goal_id
        """,
        source["goal_id"],
        *relation_scope_params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = _row_dict(row)
        if raw is None:
            continue
        target_id = _required_id(raw.get("target_goal_id"), "target_goal_id")
        if target_id == str(source["goal_id"]):
            continue
        try:
            target = await get_benchmark_transfer_target(
                pool,
                target_id,
                access_scope=access_scope,
                tenant_scope=tenant_scope,
            )
        except BenchmarkTransferError:
            continue
        _require_same_scope(source["scope"], target["scope"])
        if str(source.get("tenant_id")) != str(target.get("tenant_id")):
            continue
        specific = _required_id(raw.get("specific_goal_id"), "specific_goal_id")
        abstract = _required_id(raw.get("abstract_goal_id"), "abstract_goal_id")
        direction = SPECIFIC_TO_ABSTRACT if specific == source["goal_id"] else ABSTRACT_TO_SPECIFIC
        relation = {
            "specific_goal_id": specific,
            "abstract_goal_id": abstract,
            "relation_type": "SPECIALIZES",
            "status": "accepted",
            "confidence": raw.get("confidence"),
            "provenance": _optional_text(raw.get("provenance")),
            "decision_id": _optional_text(raw.get("decision_id")),
            "decision_metadata": _json_mapping(raw.get("decision_metadata") or {}, "relation decision metadata"),
            "decided_by": _optional_text(raw.get("decided_by")),
            "decided_at": raw.get("decided_at"),
            "updated_at": raw.get("updated_at"),
            "scope_type": raw.get("scope_type"),
            "scope_entity_id": raw.get("scope_entity_id"),
            "tenant_id": _optional_text(raw.get("tenant_id")),
            "direction": direction,
        }
        relation["relation_fingerprint"] = relation_fingerprint(relation)
        key = benchmark_transfer_idempotency_key(
            source["benchmark_id"],
            target["goal_id"],
            source_fingerprint=source["source_fingerprint"],
            target_fingerprint=target["target_fingerprint"],
            relation_fingerprint=relation["relation_fingerprint"],
            direction=direction,
            policy_version=TRANSFER_VERSION,
        )
        result.append({
            "source": dict(source),
            "target": target,
            "relation": relation,
            "direction": direction,
            "idempotency_key": key,
        })
    return result


async def _enqueue_context(
    pool: Any,
    context: Mapping[str, Any],
    *,
    max_attempts: int,
    config_version: Optional[str],
    authority: Any,
) -> tuple[int, bool]:
    validate_provenance(
        TRANSFER_PROVENANCE,
        derived=True,
        extractor_version=TRANSFER_VERSION,
    )
    source = context["source"]
    target = context["target"]
    scope = target["scope"]
    payload = {
        "source_benchmark_id": source["benchmark_id"],
        "target_goal_id": target["goal_id"],
        "direction": context["direction"],
        "source_fingerprint": source["source_fingerprint"],
        "target_fingerprint": target["target_fingerprint"],
        "relation_fingerprint": context["relation"]["relation_fingerprint"],
        "policy_version": TRANSFER_VERSION,
        "idempotency_key": context["idempotency_key"],
    }
    return await ingestion_queue.enqueue(
        pool,
        JOB_TYPE,
        payload,
        idempotency_key=context["idempotency_key"],
        source_id=source["benchmark_id"],
        scope_type=scope["scope_type"],
        scope_entity_id=scope["scope_entity_id"],
        owner_id=scope["owner_id"],
        visibility=scope["visibility"],
        config_version=config_version or TRANSFER_VERSION,
        max_attempts=max_attempts,
        offload=False,
        authority=authority,
    )


def register_handler() -> None:
    from app.services.ingestion_jobs import JOB_HANDLERS

    JOB_HANDLERS[JOB_TYPE] = handle_benchmark_transfer


async def enqueue_benchmark_transfer(
    pool: Any,
    source_benchmark_id: str,
    target_goal_id: Optional[str] = None,
    *,
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
    max_attempts: int = 5,
    config_version: Optional[str] = None,
    authority: Any = None,
) -> Any:
    register_handler()
    if target_goal_id is None:
        return await enqueue_benchmark_transfers(
            pool,
            source_benchmark_id,
            access_scope=access_scope,
            tenant_scope=tenant_scope,
            max_attempts=max_attempts,
            config_version=config_version,
            authority=authority,
        )
    context = await _load_context(
        pool,
        source_benchmark_id,
        target_goal_id,
        access_scope=access_scope,
        tenant_scope=tenant_scope or TenantScope.commons(),
    )
    return await _enqueue_context(
        pool,
        context,
        max_attempts=max_attempts,
        config_version=config_version,
        authority=authority,
    )


async def enqueue_benchmark_transfers(
    pool: Any,
    source_benchmark_id: str,
    *,
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
    max_attempts: int = 5,
    config_version: Optional[str] = None,
    authority: Any = None,
) -> list[dict[str, Any]]:
    effective_tenant = tenant_scope or TenantScope.commons()
    register_handler()
    source = await get_benchmark_transfer_source(
        pool, source_benchmark_id, access_scope=access_scope, tenant_scope=effective_tenant
    )
    contexts = await _load_direct_neighbors(
        pool, source, access_scope=access_scope, tenant_scope=effective_tenant
    )
    results: list[dict[str, Any]] = []
    for context in contexts:
        job_id, created = await _enqueue_context(
            pool,
            context,
            max_attempts=max_attempts,
            config_version=config_version,
            authority=authority,
        )
        results.append({
            "job_id": job_id,
            "created": created,
            "source_benchmark_id": source["benchmark_id"],
            "target_goal_id": context["target"]["goal_id"],
            "direction": context["direction"],
            "idempotency_key": context["idempotency_key"],
        })
    return results


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _judge_unavailable(reason: str, result: Any = None) -> SemanticJudgmentUnavailable:
    attempts = getattr(result, "attempts", None)
    return SemanticJudgmentUnavailable(reason, attempts=attempts)


def _decision_from_mapping(raw: Any) -> tuple[str, Any, float, str, dict[str, Any]]:
    if isinstance(raw, TransferDecision):
        return raw.decision, raw, raw.confidence, raw.reason, dict(raw.provenance)
    if isinstance(raw, Mapping):
        value: Any = raw
        if "verdicts" in value:
            verdicts = value.get("verdicts")
            if not isinstance(verdicts, list) or len(verdicts) != 1:
                raise _judge_unavailable("semantic transfer batch returned an invalid verdict count")
            value = verdicts[0]
        if "judgment" in value and isinstance(value["judgment"], Mapping):
            value = value["judgment"]
        decision_value = _first(value, "decision", "verdict", "relation", default=None)
        if decision_value is None:
            raise _judge_unavailable("semantic transfer reply has no decision")
        confidence = _first(value, "confidence", default=None)
        if isinstance(confidence, bool) or not isinstance(confidence, Real):
            raise _judge_unavailable("semantic transfer reply has no numeric confidence")
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value) or confidence_value < 0.0 or confidence_value > 1.0:
            raise _judge_unavailable("semantic transfer confidence is outside [0,1]")
        reason = str(_first(value, "reason", default="") or "").strip()
        provenance_value = _first(value, "provenance", default={})
        provenance = dict(provenance_value) if isinstance(provenance_value, Mapping) else {}
        return str(decision_value).strip().lower(), value, confidence_value, reason, provenance
    decision_value = getattr(raw, "decision", None) or getattr(raw, "verdict", None) or getattr(raw, "relation", None)
    if decision_value is None:
        raise _judge_unavailable("semantic transfer reply has no decision")
    confidence = getattr(raw, "confidence", None)
    if isinstance(confidence, bool) or not isinstance(confidence, Real):
        raise _judge_unavailable("semantic transfer reply has no numeric confidence")
    confidence_value = float(confidence)
    if not math.isfinite(confidence_value) or confidence_value < 0.0 or confidence_value > 1.0:
        raise _judge_unavailable("semantic transfer confidence is outside [0,1]")
    reason = str(getattr(raw, "reason", "") or "").strip()
    provenance_value = getattr(raw, "provenance", {})
    provenance = dict(provenance_value) if isinstance(provenance_value, Mapping) else {}
    return str(decision_value).strip().lower(), raw, confidence_value, reason, provenance


def _map_transfer_decision(raw_decision: str, confidence: float) -> str:
    token = raw_decision.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"transferable", "applies", "applicable", "same", "equivalent"}:
        return "transferable"
    if token in {"partial", "partially_applicable", "partially_transferable", "related"}:
        return "partial"
    if token in {"uncertain", "unknown", "unclear", "undetermined"}:
        return "uncertain"
    if token in {
        "not_transferable", "not_applicable", "inapplicable", "unrelated", "distinct", "contradicts", "reject"
    }:
        return "not_transferable"
    raise _judge_unavailable(f"unsupported semantic transfer decision {raw_decision!r}")


def _parse_judge_result(result: Any, judge: Any) -> TransferDecision:
    if result is None:
        raise _judge_unavailable("semantic transfer judge returned no result")
    if hasattr(result, "ok") and not bool(result.ok):
        raise _judge_unavailable(str(getattr(result, "reason", "") or "semantic transfer judge unavailable"), result)
    value = getattr(result, "value", result)
    if isinstance(value, Mapping) and "verdicts" in value:
        raw = value
    elif isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise _judge_unavailable("semantic transfer batch returned an invalid verdict count")
        raw = value[0]
    else:
        raw = value
    raw_decision, _, confidence, reason, parsed_provenance = _decision_from_mapping(raw)
    decision = _map_transfer_decision(raw_decision, confidence)
    if not reason:
        reason = f"semantic judge relation={raw_decision}"
    provider = getattr(result, "provider", None) or parsed_provenance.get("judge_provider")
    model = getattr(result, "model", None) or parsed_provenance.get("judge_model")
    chain = getattr(judge, "chain_id", None) or parsed_provenance.get("judge_chain")
    provenance = {
        "operation": "judge_identity_batch",
        "kind": "task_procedure",
        "judge_chain": chain,
        "judge_provider": provider,
        "judge_model": model,
        "fallback_used": bool(getattr(result, "fallback_used", False)),
        "relation": raw_decision,
    }
    return TransferDecision(decision, confidence, reason, provenance)


async def _judge_transfer(
    pool: Any,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    direction: str,
    judge: Any,
) -> TransferDecision:
    if judge is None:
        from app.ingestion.handlers import Dependencies

        judge = Dependencies.get_judge(pool)
    source_text = canonical_json({
        "benchmark": {
            "name": source["name"],
            "description": source["description"],
            "version": source["version"],
            "evaluation_protocol": source["evaluation_protocol"],
            "environment_specification": source["environment_specification"],
            "success_criteria": source["success_criteria"],
            "comparison_policy": source["comparison_policy"],
            "invariants": source["invariants"],
        },
        "source_goal": {
            "name": source["goal_name"],
            "description": source["goal_description"],
            "objective": source["goal_objective"],
        },
    })
    target_text = canonical_json({
        "goal": {
            "name": target["name"],
            "description": target["description"],
            "objective": target["objective"],
            "constraints": target["constraints"],
            "expected_outcome": target["expected_outcome"],
            "verification_requirement": target["verification_requirement"],
        },
        "direction": direction,
    })
    method = getattr(judge, "judge_identity_batch", None)
    if not callable(method):
        method = getattr(judge, "judge_batch", None)
    if not callable(method):
        raise _judge_unavailable("configured judge has no batch identity operation")
    result = await _maybe_await(method("task_procedure", target_text, [source_text]))
    return _parse_judge_result(result, judge)


def _lineage_provenance(
    context: Mapping[str, Any],
    decision: TransferDecision,
) -> dict[str, Any]:
    source = context["source"]
    target = context["target"]
    relation = context["relation"]
    judge = decision.provenance
    return {
        "transfer": TRANSFER_PROVENANCE,
        "extractor_version": TRANSFER_VERSION,
        "policy_version": TRANSFER_VERSION,
        "source_benchmark_id": source["benchmark_id"],
        "source_goal_id": source["goal_id"],
        "target_goal_id": target["goal_id"],
        "direction": context["direction"],
        "judge_chain": judge.get("judge_chain"),
        "judge_provider": judge.get("judge_provider"),
        "judge_model": judge.get("judge_model"),
        "prompt_version": TRANSFER_VERSION,
        "source": {
            "benchmark_id": source["benchmark_id"],
            "goal_id": source["goal_id"],
            "version": source["version"],
            "name": source["name"],
            "fingerprint": source["source_fingerprint"],
        },
        "target": {
            "goal_id": target["goal_id"],
            "name": target["name"],
            "fingerprint": target["target_fingerprint"],
            "scope": target["scope"],
        },
        "relation": {
            "type": relation["relation_type"],
            "status": relation["status"],
            "specific_goal_id": relation["specific_goal_id"],
            "abstract_goal_id": relation["abstract_goal_id"],
            "confidence": relation.get("confidence"),
            "provenance": relation.get("provenance"),
            "decision_id": relation.get("decision_id"),
            "decided_at": relation.get("decided_at"),
            "updated_at": relation.get("updated_at"),
            "fingerprint": relation["relation_fingerprint"],
            "direction": relation["direction"],
        },
        "judge": {
            "operation": judge.get("operation"),
            "kind": judge.get("kind"),
            "judge_chain": judge.get("judge_chain"),
            "judge_provider": judge.get("judge_provider"),
            "judge_model": judge.get("judge_model"),
            "fallback_used": judge.get("fallback_used", False),
            "relation": judge.get("relation"),
        },
    }


def _target_name(source: Mapping[str, Any], target: Mapping[str, Any], key: str) -> str:
    suffix = key.rsplit(":", 1)[-1][:16]
    return f"{source['name']} for {target['name']} [benchmark transfer {suffix}]"


def _target_metadata(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    context: Mapping[str, Any],
    decision: TransferDecision,
) -> dict[str, Any]:
    return {
        "invariants": list(source["invariants"]),
        "benchmark_transfer": {
            "transfer_key": context["idempotency_key"],
            "source_benchmark_id": source["benchmark_id"],
            "source_goal_id": source["goal_id"],
            "target_goal_id": target["goal_id"],
            "direction": context["direction"],
            "source_fingerprint": source["source_fingerprint"],
            "target_fingerprint": target["target_fingerprint"],
            "relation_fingerprint": context["relation"]["relation_fingerprint"],
            "policy_version": TRANSFER_VERSION,
            "decision": decision.decision,
            "confidence": decision.confidence,
        },
    }


async def _get_or_insert_benchmark(
    conn: Any,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    context: Mapping[str, Any],
    decision: TransferDecision,
) -> dict[str, Any]:
    name = _target_name(source, target, context["idempotency_key"])
    existing = await conn.fetchrow(
        "SELECT * FROM benchmarks WHERE goal_id = $1 AND name = $2 AND version = 1 LIMIT 1",
        target["goal_id"], name,
    )
    created = False
    if existing is None:
        existing = await conn.fetchrow(
            """
            INSERT INTO benchmarks (
                id, goal_id, name, description, version, evaluation_protocol,
                environment_specification, success_criteria, comparison_policy,
                status, provenance, metadata
            ) VALUES ($1,$2,$3,$4,1,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb,'draft',$9,$10::jsonb)
            ON CONFLICT (goal_id, name, version) DO NOTHING
            RETURNING *
            """,
            str(uuid7()), target["goal_id"], name, source["description"],
            _json_text(source["evaluation_protocol"]),
            _json_text(source["environment_specification"]),
            _json_text(source["success_criteria"]),
            _json_text(source["comparison_policy"]),
            TRANSFER_PROVENANCE,
            _json_text(_target_metadata(source, target, context, decision)),
        )
        created = existing is not None
    if existing is None:
        existing = await conn.fetchrow(
            "SELECT * FROM benchmarks WHERE goal_id = $1 AND name = $2 AND version = 1 LIMIT 1",
            target["goal_id"], name,
        )
    row = _row_dict(existing)
    if row is None:
        raise BenchmarkTransferError("target Benchmark could not be materialized")
    if str(_first(row, "goal_id")) != str(target["goal_id"]):
        raise BenchmarkTransferError("materialized Benchmark belongs to a different Goal")
    metadata = _json_mapping(_first(row, "metadata", default={}), "target Benchmark metadata")
    transfer_metadata = metadata.get("benchmark_transfer")
    expected_metadata = _target_metadata(source, target, context, decision)["benchmark_transfer"]
    if not isinstance(transfer_metadata, Mapping) or dict(transfer_metadata) != expected_metadata:
        raise BenchmarkTransferError("Benchmark name is occupied by unrelated content")
    expected_content = {
        "description": source["description"],
        "version": 1,
        "evaluation_protocol": source["evaluation_protocol"],
        "environment_specification": source["environment_specification"],
        "success_criteria": source["success_criteria"],
        "comparison_policy": source["comparison_policy"],
        "provenance": TRANSFER_PROVENANCE,
    }
    for field, expected in expected_content.items():
        actual = _first(row, field, default=None)
        if isinstance(expected, (Mapping, list)):
            actual = _json_value(actual, field, None)
        if actual != expected:
            raise BenchmarkTransferError("materialized Benchmark content changed")
    if str(_first(row, "status", default="draft")) not in ("draft", "active", "frozen", "deprecated"):
        raise BenchmarkTransferError("materialized Benchmark has an invalid status")
    return row, created


async def _get_or_insert_submission(
    conn: Any,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    context: Mapping[str, Any],
    decision: TransferDecision,
) -> dict[str, Any]:
    name = _target_name(source, target, context["idempotency_key"])
    existing = await conn.fetchrow(
        """
        SELECT * FROM benchmark_submissions
        WHERE benchmark_transfer_key = $1
        ORDER BY created_at ASC LIMIT 1
        """,
        context["idempotency_key"],
    )
    created = False
    if existing is None:
        status_reason = (
            f"semantic benchmark transfer decision={decision.decision}; "
            f"confidence={decision.confidence:.4f}; {decision.reason}"
        )
        existing = await conn.fetchrow(
            """
            INSERT INTO benchmark_submissions (
                id, goal_id, benchmark_id, name, description, success_criteria,
                invariants, verification_method, status, status_reason,
                layer1_result, layer2_result, submitted_by, provenance,
                scope_type, scope_entity_id, owner_id, visibility, benchmark_transfer_key
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,'needs_review',$9,
                      $10::jsonb,$11::jsonb,$12,$13,$14,$15,$16,$17,$18)
            RETURNING *
            """,
            str(uuid7()), target["goal_id"], benchmark["id"], name, source["description"],
            _json_text(source["success_criteria"]), _json_text(source["invariants"]), _json_text({}),
            status_reason, _json_text({}), _json_text({}), TRANSFER_ACTOR, TRANSFER_PROVENANCE,
            target["scope"]["scope_type"], target["scope"]["scope_entity_id"],
            target["scope"]["owner_id"], target["scope"]["visibility"], context["idempotency_key"],
        )
        created = existing is not None
    row = _row_dict(existing)
    if row is None:
        raise BenchmarkTransferError("target Benchmark submission could not be materialized")
    if str(_first(row, "goal_id")) != str(target["goal_id"]):
        raise BenchmarkTransferError("materialized submission belongs to a different Goal")
    if str(_first(row, "benchmark_id")) != str(benchmark["id"]):
        raise BenchmarkTransferError("materialized submission belongs to a different Benchmark")
    expected_scope = {
        "scope_type": target["scope"]["scope_type"],
        "scope_entity_id": target["scope"]["scope_entity_id"],
        "owner_id": target["scope"]["owner_id"],
        "visibility": target["scope"]["visibility"],
    }
    for field, expected in expected_scope.items():
        if _first(row, field, default=None) != expected:
            raise BenchmarkTransferError("materialized submission scope changed")
    for field, expected in (
        ("description", source["description"]),
        ("success_criteria", source["success_criteria"]),
        ("invariants", source["invariants"]),
    ):
        actual = _first(row, field, default=None)
        if isinstance(expected, (Mapping, list)):
            actual = _json_value(actual, field, None)
        if actual != expected:
            raise BenchmarkTransferError("materialized submission content changed")
    return row, created


async def _read_goal_projection(conn: Any, goal_id: str) -> dict[str, Any]:
    row = _row_dict(await conn.fetchrow(
        """
        SELECT goal_id::text AS id, status AS projected_status,
               version AS projected_version, scope_type AS projected_scope_type,
               scope_entity_id AS projected_scope_entity_id,
               visibility::text AS projected_visibility,
               owner_id AS projected_owner_id, home_shard_id
        FROM goal_search_index
        WHERE goal_id = $1::uuid
        """,
        goal_id,
    ))
    if row is None:
        raise BenchmarkTransferStale("Goal projection changed during transfer")
    return row


async def _read_goal(
    pool: Any,
    goal_id: str,
    *,
    central_conn: Any = None,
) -> dict[str, Any]:
    owner = await home_pool(pool, "goal", goal_id)
    conn = central_conn if central_conn is not None and owner is pool else owner
    row = _row_dict(await conn.fetchrow(
        """
        SELECT id::text AS id, canonical_name, description, objective, constraints,
               expected_outcome, verification_requirement, version, status, t_invalid,
               scope_type, scope_entity_id, visibility::text AS visibility,
               owner_id, home_shard_id
        FROM goals
        WHERE id = $1::uuid AND t_invalid IS NULL
        """,
        goal_id,
    ))
    if row is None:
        raise BenchmarkTransferStale("Goal changed during transfer")
    return row


def _assert_goal_projection_current(
    expected: Mapping[str, Any],
    projection: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> None:
    expected_scope = _scope_key_for_goal(expected.get("scope", expected))
    projection_scope = (
        str(projection.get("projected_scope_type") or "global"),
        None
        if str(projection.get("projected_scope_type") or "global") == "global"
        else str(projection.get("projected_scope_entity_id") or "") or None,
    )
    expected_home = str(expected.get("home_shard_id") or HOME_SHARD)
    if (
        str(projection.get("projected_status")) != str(expected.get("goal_status", expected.get("status")))
        or int(projection.get("projected_version") or 0) != int(expected.get("goal_version", expected.get("version")) or 0)
        or projection_scope != expected_scope
        or str(projection.get("projected_visibility")) != str(expected.get("visibility") or expected["scope"]["visibility"])
        or projection.get("projected_owner_id") != expected.get("owner_id", expected["scope"]["owner_id"])
        or str(projection.get("home_shard_id") or HOME_SHARD) != expected_home
        or str(canonical.get("home_shard_id") or HOME_SHARD) != expected_home
        or _scope_key_for_goal(canonical) != expected_scope
    ):
        raise BenchmarkTransferStale("Goal projection or content changed during transfer")


async def _lock_and_validate_relation(
    conn: Any, context: Mapping[str, Any]
) -> dict[str, Any]:
    source_goal_id = context["source"]["goal_id"]
    target_goal_id = context["target"]["goal_id"]
    row = _row_dict(await conn.fetchrow(
        """
        SELECT specific_goal_id::text AS specific_goal_id,
               abstract_goal_id::text AS abstract_goal_id, relation_type, status,
               confidence, provenance, decision_id::text AS decision_id,
               decision_metadata, decided_by, decided_at, updated_at,
               scope_type, scope_entity_id, tenant_id::text AS tenant_id
        FROM goal_relations
        WHERE relation_type = 'SPECIALIZES' AND status = 'accepted'
          AND ((specific_goal_id = $1 AND abstract_goal_id = $2)
            OR (specific_goal_id = $2 AND abstract_goal_id = $1))
        FOR UPDATE
        """,
        source_goal_id,
        target_goal_id,
    ))
    if row is None:
        raise BenchmarkTransferStale("accepted Goal relation changed before transfer write")
    current = {
        **row,
        "specific_goal_id": str(row["specific_goal_id"]),
        "abstract_goal_id": str(row["abstract_goal_id"]),
        "decision_id": _optional_text(row.get("decision_id")),
        "decision_metadata": _json_mapping(row.get("decision_metadata") or {}, "relation decision metadata"),
        "decided_by": _optional_text(row.get("decided_by")),
        "scope_entity_id": _optional_text(row.get("scope_entity_id")),
        "tenant_id": _optional_text(row.get("tenant_id")),
        "direction": context["relation"].get("direction"),
    }
    current["relation_fingerprint"] = relation_fingerprint(current)
    if current["relation_fingerprint"] != context["relation"]["relation_fingerprint"]:
        raise BenchmarkTransferStale("accepted Goal relation decision changed before transfer write")
    return current


async def _validate_transfer_state(
    pool: Any,
    control: Any,
    context: Mapping[str, Any],
    effective_tenant: TenantScope,
) -> dict[str, Any]:
    source = context["source"]
    target = context["target"]
    benchmark = _row_dict(await control.fetchrow(
        """
        SELECT id AS benchmark_id, goal_id AS source_goal_id, name AS benchmark_name,
               description AS benchmark_description, version AS benchmark_version,
               evaluation_protocol AS benchmark_evaluation_protocol,
               environment_specification AS benchmark_environment_specification,
               success_criteria AS benchmark_success_criteria,
               comparison_policy AS benchmark_comparison_policy,
               status AS benchmark_status, frozen_at AS benchmark_frozen_at,
               provenance AS benchmark_provenance, metadata AS benchmark_metadata
        FROM benchmarks
        WHERE id = $1::uuid
        FOR SHARE
        """,
        source["benchmark_id"],
    ))
    if benchmark is None:
        raise BenchmarkTransferStale("source Benchmark disappeared during transfer")
    if str(benchmark["source_goal_id"]) != str(source["goal_id"]):
        raise BenchmarkTransferStale("source Benchmark changed Goal during transfer")
    source_projection = await _read_goal_projection(control, str(source["goal_id"]))
    target_projection = await _read_goal_projection(control, str(target["goal_id"]))
    source_goal = await _read_goal(pool, str(source["goal_id"]), central_conn=control)
    target_goal = await _read_goal(pool, str(target["goal_id"]), central_conn=control)
    source_goal["tenant_id"] = str(effective_tenant.tenant_id) if effective_tenant.tenant_id else None
    target_goal["tenant_id"] = str(effective_tenant.tenant_id) if effective_tenant.tenant_id else None
    _assert_goal_projection_current(source, source_projection, source_goal)
    _assert_goal_projection_current(target, target_projection, target_goal)
    try:
        current_source = _source_from_rows(benchmark, source_goal)
        current_target = _target_from_goal(target_goal)
    except (BenchmarkSourceInvalid, BenchmarkTransferError) as exc:
        raise BenchmarkTransferStale("Goal or Benchmark content changed during transfer") from exc
    if current_source["source_fingerprint"] != source["source_fingerprint"]:
        raise BenchmarkTransferStale("source Benchmark fingerprint changed during transfer")
    if current_target["target_fingerprint"] != target["target_fingerprint"]:
        raise BenchmarkTransferStale("target Goal fingerprint changed during transfer")
    relation = await _lock_and_validate_relation(control, context)
    return {"source": current_source, "target": current_target, "relation": relation}


async def _load_lineage(pool: Any, key: str) -> Optional[dict[str, Any]]:
    row = await pool.fetchrow(
        "SELECT * FROM benchmark_transfer_decisions WHERE transfer_key = $1 LIMIT 1",
        key,
    )
    return _row_dict(row)


def _lineage_result(row: Any, *, replayed: bool) -> dict[str, Any]:
    result = _row_dict(row)
    if result is None:
        raise BenchmarkTransferError("benchmark transfer lineage could not be read")
    provenance = result.get("provenance")
    if isinstance(provenance, str):
        try:
            result["provenance"] = json.loads(provenance)
        except (TypeError, ValueError):
            result["provenance"] = {}
    for field in (
        "id", "source_benchmark_id", "source_goal_id", "target_goal_id",
        "target_benchmark_id", "benchmark_submission_id",
    ):
        if result.get(field) is not None:
            result[field] = str(result[field])
    result["replayed"] = replayed
    return result


async def _compensate_transfer(
    control: Any,
    *,
    lineage_id: Optional[str],
    submission_id: Optional[str],
    benchmark_id: Optional[str],
    lineage_created: bool,
    submission_created: bool,
    benchmark_created: bool,
) -> None:
    await control.execute("SELECT set_config('app.benchmark_transfer_compensation', 'on', true)")
    try:
        if lineage_created and lineage_id is not None:
            await control.execute(
                "DELETE FROM benchmark_transfer_decisions WHERE id = $1::uuid",
                str(lineage_id),
            )
        if submission_created and submission_id is not None:
            await control.execute(
                "DELETE FROM benchmark_submissions WHERE id = $1::uuid",
                str(submission_id),
            )
        if benchmark_created and benchmark_id is not None:
            await control.execute("DELETE FROM benchmarks WHERE id = $1::uuid", str(benchmark_id))
    finally:
        await control.execute("SELECT set_config('app.benchmark_transfer_compensation', 'off', true)")


async def _persist_decision(
    pool: Any,
    context: Mapping[str, Any],
    decision: TransferDecision,
    *,
    job: Mapping[str, Any],
    tenant_scope: Optional[TenantScope],
) -> dict[str, Any]:
    source = context["source"]
    target = context["target"]
    scope = target["scope"]
    job_id = job.get("id") if job else None
    if job_id is not None:
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise BenchmarkTransferError("job id must be a positive integer")
    effective_tenant = tenant_scope or TenantScope.commons()
    effective_tenant_id = str(effective_tenant.tenant_id or COMMONS_TENANT_ID)
    if str(source.get("tenant_id")) != effective_tenant_id:
        raise BenchmarkTransferPrivacyError("source Benchmark tenant changed before transfer write")
    if str(target.get("tenant_id")) != effective_tenant_id:
        raise BenchmarkTransferPrivacyError("target Goal tenant changed before transfer write")
    target_benchmark: Optional[dict[str, Any]] = None
    target_benchmark_created = False
    submission: Optional[dict[str, Any]] = None
    submission_created = False
    async with AsyncExitStack() as stack:
        control = await stack.enter_async_context(tenant_transaction(pool, effective_tenant))
        await control.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"benchmark-transfer:{context['idempotency_key']}",
        )
        existing_lineage = await control.fetchrow(
            "SELECT * FROM benchmark_transfer_decisions WHERE transfer_key = $1 LIMIT 1 FOR UPDATE",
            context["idempotency_key"],
        )
        if existing_lineage is not None:
            return _lineage_result(existing_lineage, replayed=True)
        state = await _validate_transfer_state(pool, control, context, effective_tenant)
        write_context = {
            **context,
            "source": state["source"],
            "target": state["target"],
            "relation": state["relation"],
        }
        if decision.decision in ("transferable", "partial"):
            target_benchmark, target_benchmark_created = await _get_or_insert_benchmark(
                control, state["source"], state["target"], write_context, decision
            )
            submission, submission_created = await _get_or_insert_submission(
                control, state["source"], state["target"], target_benchmark, write_context, decision
            )
        lineage_provenance = _lineage_provenance(write_context, decision)
        row = await control.fetchrow(
            """
            INSERT INTO benchmark_transfer_decisions (
                id, transfer_key, source_benchmark_id, source_goal_id, target_goal_id,
                direction, transfer_direction, decision, confidence, reason, provenance,
                source_fingerprint, target_fingerprint, relation_fingerprint,
                policy_version, judge_chain, judge_provider, judge_model,
                prompt_version, job_id, target_benchmark_id, benchmark_submission_id,
                scope_type, scope_entity_id, owner_id, visibility, tenant_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26::uuid)
            ON CONFLICT (transfer_key) DO NOTHING
            RETURNING *
            """,
            str(uuid7()), context["idempotency_key"], state["source"]["benchmark_id"], state["source"]["goal_id"], state["target"]["goal_id"],
            context["direction"], SOURCE_TO_TARGET, decision.decision, decision.confidence, decision.reason,
            _json_text(lineage_provenance), state["source"]["source_fingerprint"], state["target"]["target_fingerprint"],
            state["relation"]["relation_fingerprint"], TRANSFER_VERSION,
            decision.provenance.get("judge_chain"), decision.provenance.get("judge_provider"),
            decision.provenance.get("judge_model"), TRANSFER_VERSION, job_id,
            target_benchmark.get("id") if target_benchmark else None,
            submission.get("id") if submission else None,
            scope["scope_type"], scope["scope_entity_id"], scope["owner_id"], scope["visibility"], effective_tenant_id,
        )
        if row is None:
            row = await control.fetchrow(
                "SELECT * FROM benchmark_transfer_decisions WHERE transfer_key = $1 LIMIT 1",
                context["idempotency_key"],
            )
            return _lineage_result(row, replayed=True)
        lineage_id = str(row["id"])
        try:
            await _validate_transfer_state(pool, control, context, effective_tenant)
        except Exception as exc:
            await _compensate_transfer(
                control,
                lineage_id=lineage_id,
                submission_id=str(submission["id"]) if submission else None,
                benchmark_id=str(target_benchmark["id"]) if target_benchmark else None,
                lineage_created=True,
                submission_created=submission_created,
                benchmark_created=target_benchmark_created,
            )
            raise BenchmarkTransferStale("benchmark transfer post-validation failed; retry required") from exc
    return _lineage_result(row, replayed=False)


def _job_scope(job: Mapping[str, Any]) -> dict[str, Any]:
    return _scope_from_row({
        "scope_type": job.get("scope_type"),
        "scope_entity_id": job.get("scope_entity_id"),
        "visibility": job.get("visibility") or "public",
        "owner_id": job.get("owner_id"),
    })


def _access_scope_from_job(job: Mapping[str, Any]) -> AccessScope:
    scope = _job_scope(job)
    if scope["visibility"] == "private":
        return AccessScope.for_user(scope["owner_id"])
    return AccessScope.anonymous()


def _tenant_scope_from_job(job: Mapping[str, Any]) -> TenantScope:
    if not job:
        return TenantScope.commons()
    scope = _job_scope(job)
    if scope["visibility"] == "org":
        return TenantScope.for_tenant(str(scope["scope_entity_id"]))
    return TenantScope.commons()


def _validate_job(
    job: Mapping[str, Any],
    context: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    if not job:
        return
    job_scope = _job_scope(job)
    if not _same_scope(job_scope, context["source"]["scope"]):
        raise BenchmarkTransferPrivacyError("job scope does not match the source Benchmark scope")
    if not _same_scope(job_scope, context["target"]["scope"]):
        raise BenchmarkTransferPrivacyError("job scope does not match the target Goal scope")
    job_key = job.get("idempotency_key")
    if job_key is None or str(job_key) != context["idempotency_key"]:
        raise BenchmarkTransferError("job idempotency key does not match the current source and target")
    payload_key = payload.get("idempotency_key")
    if payload_key is None or str(payload_key) != context["idempotency_key"]:
        raise BenchmarkTransferError("payload idempotency key does not match the current source and target")
    payload_direction = payload.get("direction")
    if payload_direction is None or str(payload_direction) != context["direction"]:
        raise BenchmarkTransferError("payload direction does not match the accepted Goal edge")
    for field, expected in (
        ("source_fingerprint", context["source"]["source_fingerprint"]),
        ("target_fingerprint", context["target"]["target_fingerprint"]),
        ("relation_fingerprint", context["relation"]["relation_fingerprint"]),
        ("policy_version", TRANSFER_VERSION),
    ):
        supplied = payload.get(field)
        if supplied is None or str(supplied) != str(expected):
            raise BenchmarkTransferError(f"payload {field} is stale")


async def handle_benchmark_transfer(
    pool: Any,
    payload: Mapping[str, Any],
    *,
    judge: Any = None,
    access_scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    body = _row_dict(payload)
    if body is None:
        raise BenchmarkTransferError("benchmark transfer payload is required")
    source_id = _first(body, "source_benchmark_id", "source_id")
    target_id = _first(body, "target_goal_id", "target_id")
    if source_id is None or target_id is None:
        raise BenchmarkTransferError("benchmark transfer payload requires source and target ids")
    raw_job = body.get("_job")
    if raw_job is not None and not isinstance(raw_job, Mapping):
        raise BenchmarkTransferError("job metadata must be an object")
    job: dict[str, Any] = dict(raw_job or {})
    effective_scope = access_scope
    if effective_scope is None:
        effective_scope = _access_scope_from_job(job) if job else AccessScope.anonymous()
    effective_tenant = tenant_scope or _tenant_scope_from_job(job)
    context = await _load_context(
        pool,
        str(source_id),
        str(target_id),
        access_scope=effective_scope,
        tenant_scope=effective_tenant,
    )
    _validate_job(job, context, body)
    prior = await _load_lineage(pool, context["idempotency_key"])
    if prior is not None:
        return _lineage_result(prior, replayed=True)
    decision = await _judge_transfer(
        pool, context["source"], context["target"], context["direction"], judge
    )
    return await _persist_decision(
        pool, context, decision, job=job, tenant_scope=effective_tenant
    )


handle_benchmark_transfer_job = handle_benchmark_transfer
handle_transfer_job = handle_benchmark_transfer
process_benchmark_transfer = handle_benchmark_transfer
process_transfer_job = handle_benchmark_transfer
enqueue_transfer = enqueue_benchmark_transfer

register_handler()
