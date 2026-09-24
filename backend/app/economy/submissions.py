"""Contribution submission and review services.

Submission identity, provenance, scope, and visibility are established at the
authenticated service boundary.  A submission is an audit snapshot in front of
an existing Procedure or Benchmark; it is not a second lifecycle state machine.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Optional

import asyncpg

from app.economy import credits as credits_service
from app.economy import verification as verification_service
from app.economy.duplicates import (
    embed_submission_text,
    score_against_parent,
    score_benchmark_duplicate,
    score_procedure_duplicate,
    submission_dedup_text,
)
from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import Embedder, to_pgvector
from app.services.procedures import capture_procedure, get_procedure
from app.services.product_model import associate_solution, create_benchmark, get_goal_for_product
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7

SUBMISSION_PROVENANCE = "system_pending_review"

_PROCEDURE_DETAIL_COLUMNS = (
    "s.id, s.goal_id, s.submission_type, s.parent_procedure_row_id, s.procedure_row_id, "
    "s.name, s.content, s.rationale, s.applicability_context, s.constraints, "
    "s.implementation_requirements, s.supporting_evidence, s.status, s.status_reason, "
    "s.layer1_result, s.layer2_result, s.duplicate_of_submission_id, s.duplicate_score, "
    "s.parent_similarity_score, s.submitted_by, s.reviewed_by, s.reviewed_at, s.provenance, "
    "s.scope_type, s.scope_entity_id, s.owner_id, s.visibility, s.created_at, s.updated_at"
)
_PROCEDURE_LIST_COLUMNS = (
    "s.id, s.goal_id, s.submission_type, s.parent_procedure_row_id, s.procedure_row_id, "
    "s.name, s.rationale, s.applicability_context, s.constraints, s.implementation_requirements, "
    "s.status, s.status_reason, s.layer1_result, s.layer2_result, s.duplicate_of_submission_id, "
    "s.duplicate_score, s.parent_similarity_score, s.submitted_by, s.reviewed_by, s.reviewed_at, "
    "s.provenance, s.scope_type, s.scope_entity_id, s.owner_id, s.visibility, s.created_at, s.updated_at"
)
_BENCHMARK_DETAIL_COLUMNS = (
    "s.id, s.goal_id, s.benchmark_id, s.name, s.description, s.success_criteria, s.invariants, "
    "s.verification_method, s.status, s.status_reason, s.layer1_result, s.layer2_result, "
    "s.duplicate_of_submission_id, s.duplicate_score, s.submitted_by, s.reviewed_by, s.reviewed_at, "
    "s.provenance, s.scope_type, s.scope_entity_id, s.owner_id, s.visibility, s.created_at, s.updated_at, "
    "(SELECT count(*) FROM evaluations e WHERE e.benchmark_id = s.benchmark_id "
    "AND e.status = 'completed') AS _evaluation_count, "
    "(SELECT count(*) FROM evaluations e WHERE e.benchmark_id = s.benchmark_id "
    "AND e.status = 'completed' AND e.aggregate_result = 'pass') AS _pass_count, "
    "(SELECT count(*) FROM evaluations e WHERE e.benchmark_id = s.benchmark_id "
    "AND e.status = 'completed' AND e.aggregate_result = 'fail') AS _fail_count"
)
_BENCHMARK_LIST_COLUMNS = (
    "s.id, s.goal_id, s.benchmark_id, s.name, s.description, s.success_criteria, s.invariants, "
    "s.verification_method, s.status, s.status_reason, s.layer1_result, s.layer2_result, "
    "s.duplicate_of_submission_id, s.duplicate_score, s.submitted_by, s.reviewed_by, s.reviewed_at, "
    "s.provenance, s.scope_type, s.scope_entity_id, s.owner_id, s.visibility, s.created_at, s.updated_at"
)
_USAGE_COLUMNS = (
    "u.id, u.procedure_row_id, u.procedure_id, u.goal_id, u.benchmark_id, u.is_self_use, "
    "u.outcome_state, u.verification_layer, u.context_key, u.created_at"
)
_JSON_FIELDS = frozenset({
    "content", "applicability_context", "constraints", "implementation_requirements",
    "supporting_evidence", "layer1_result", "layer2_result", "success_criteria",
    "invariants", "verification_method",
})


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field} is required and must be a non-empty object")
    return dict(value)


def _required_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} is required and must be a non-empty list")
    return list(value)


def _validate_preconditions(value: Any) -> list[dict[str, Any]]:
    preconditions = _required_list(value, "preconditions")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(preconditions):
        if not isinstance(item, Mapping):
            raise ValueError(f"preconditions[{index}] must be an object")
        if not str(item.get("subject", "")).strip() or not str(item.get("predicate", "")).strip() or "value" not in item:
            raise ValueError(f"preconditions[{index}] must include subject, predicate, and value")
        normalized.append(dict(item))
    return normalized


def _validate_procedure_payload(
    *,
    rationale: Any,
    preconditions: Any,
    expected_outcome: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    return (
        _required_text(rationale, "rationale"),
        _validate_preconditions(preconditions),
        _required_mapping(expected_outcome, "expected_outcome"),
    )


def _validate_benchmark_payload(
    *,
    description: Any,
    success_criteria: Any,
    failure_criteria: Any,
    scope_conditions: Any,
) -> tuple[str, dict[str, Any], list[Any], list[Any]]:
    return (
        _required_text(description, "description"),
        _required_mapping(success_criteria, "success_criteria"),
        _required_list(failure_criteria, "failure_criteria"),
        _required_list(scope_conditions, "scope_conditions"),
    )


def _goal_submission_metadata(goal: Mapping[str, Any], access_scope: AccessScope) -> dict[str, Any]:
    visibility = goal.get("visibility")
    if visibility not in ("public", "private"):
        visibility = "private" if access_scope.viewer_id else "public"
    scope_type = goal.get("scope_type")
    scope_entity_id = goal.get("scope_entity_id")
    if not scope_type:
        scope_type = "user" if visibility == "private" and access_scope.viewer_id else "global"
    if scope_type == "global":
        scope_entity_id = None
    elif not scope_entity_id:
        scope_entity_id = access_scope.viewer_id or goal.get("owner_id")
    resolved_scope_type, resolved_scope_entity_id = validate_scope(
        str(scope_type), scope_entity_id, allow_global_entity_id=False
    )
    return {
        "provenance": SUBMISSION_PROVENANCE,
        "scope_type": resolved_scope_type,
        "scope_entity_id": resolved_scope_entity_id,
        "visibility": visibility,
    }


def _scope(scope: Optional[AccessScope]) -> AccessScope:
    return scope or AccessScope.anonymous()


def _project(row: Any, columns: tuple[str, ...]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    values = dict(row)
    result: dict[str, Any] = {}
    for column in columns:
        key = column.rsplit(".", 1)[-1]
        if key in values:
            value = values[key]
            if key in _JSON_FIELDS and isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass
            result[key] = value
    return result


def _detail_projection(row: Any, columns: tuple[str, ...]) -> Optional[dict[str, Any]]:
    return _project(row, columns)


def _list_projection(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
    return _project(row, columns) or {}


def _visibility_pair(scope: AccessScope, *, first_index: int) -> tuple[str, str, list[Any]]:
    first_sql, first_params, next_index = scope_predicates(
        scope, TenantScope.unrestricted(), alias="s", param_index=first_index
    )
    second_sql, second_params, _ = scope_predicates(
        scope, TenantScope.unrestricted(), alias="g", param_index=next_index
    )
    return first_sql, second_sql, [*first_params, *second_params]


def _row_visible(row: Mapping[str, Any], scope: AccessScope) -> bool:
    if scope.is_unrestricted or scope.viewer_id is None:
        return True
    if "visibility" not in row:
        return True
    return row.get("visibility") == "public" or row.get("owner_id") == scope.viewer_id


async def create_procedure_submission(
    pool: asyncpg.Pool,
    *,
    goal_id: str,
    submission_type: str,
    name: str,
    steps: list,
    actor_subject: str,
    rationale: Optional[str] = None,
    preconditions: Optional[list] = None,
    expected_outcome: Optional[dict] = None,
    expected_effects: Optional[list] = None,
    postconditions: Optional[list] = None,
    failure_conditions: Optional[list] = None,
    existing_evidence: Optional[list] = None,
    previous_executions: Optional[list] = None,
    known_failure_modes: Optional[list] = None,
    applicability_context: Optional[dict] = None,
    constraints: Optional[list] = None,
    implementation_requirements: Optional[dict] = None,
    supporting_evidence: Optional[list] = None,
    parent_procedure_row_id: Optional[str] = None,
    provenance: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    visibility: str = "public",
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
    embedder: Optional[Embedder] = None,
    llm_evaluator=None,
) -> dict[str, Any]:
    del provenance, scope_type, scope_entity_id, visibility
    if submission_type not in ("new", "improvement"):
        raise ValueError("submission_type must be 'new' or 'improvement'")
    if submission_type == "improvement" and not parent_procedure_row_id:
        raise ValueError("an 'improvement' submission requires parent_procedure_row_id")
    name = _required_text(name, "name")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps are required and must be a non-empty list")
    rationale, preconditions, expected_outcome = _validate_procedure_payload(
        rationale=rationale,
        preconditions=preconditions,
        expected_outcome=expected_outcome,
    )
    access_scope = _scope(access_scope)
    goal = await get_goal_for_product(pool, goal_id, scope=access_scope)
    if goal is None:
        raise ValueError(f"goal {goal_id} not found or not visible")
    metadata = _goal_submission_metadata(goal, access_scope)
    validate_provenance(metadata["provenance"])

    layer1 = verification_service.evaluate_layer1(
        name=name,
        steps=steps,
        rationale=rationale,
        preconditions=preconditions,
        expected_outcome=expected_outcome,
    )
    dedup_text = submission_dedup_text(name=name, rationale=rationale, steps=steps)
    embedding = await embed_submission_text(embedder, dedup_text)
    duplicate = await score_procedure_duplicate(
        pool, goal_id=goal_id, embedding=embedding, scope=access_scope, tenant_scope=tenant_scope
    )
    parent_similarity = None
    if submission_type == "improvement":
        parent_similarity = await score_against_parent(
            pool,
            parent_procedure_row_id=parent_procedure_row_id,
            embedding=embedding,
            scope=access_scope,
            tenant_scope=tenant_scope,
        )
    layer2 = await verification_service.evaluate_layer2(
        submission={
            "name": name,
            "rationale": rationale,
            "preconditions": preconditions,
            "expected_outcome": expected_outcome,
            "goal": goal,
        },
        duplicate=duplicate,
        layer1=layer1,
        parent_similarity=parent_similarity,
        llm_evaluator=llm_evaluator,
    )
    status = "candidate" if layer2["decision"] == "candidate" else "needs_review"
    status_reason = None
    if status == "needs_review":
        status_reason = "; ".join(layer2.get("notes", [])) or "flagged by automated review"

    conflicting_benchmark = await pool.fetchval(
        "SELECT id FROM benchmark_submissions WHERE goal_id = $1 AND submitted_by = $2 AND status <> 'rejected' LIMIT 1",
        goal_id,
        actor_subject,
    )
    if conflicting_benchmark is not None:
        status = "needs_review"
        conflict_note = (
            f"submitter also has a Benchmark submission ({conflicting_benchmark}) for this same Goal "
            "-- review for independence before acceptance"
        )
        status_reason = f"{status_reason}; {conflict_note}" if status_reason else conflict_note

    content = {
        "steps": list(steps or []),
        "preconditions": preconditions,
        "expected_outcome": expected_outcome,
        "expected_effects": list(expected_effects or []),
        "postconditions": list(postconditions or []),
        "failure_conditions": list(failure_conditions or []),
        "implementation_requirements": dict(implementation_requirements or {}),
        "previous_executions": list(previous_executions or []),
        "known_failure_modes": list(known_failure_modes or []),
    }
    submitted_evidence = existing_evidence if existing_evidence else supporting_evidence
    row_id = str(uuid7())
    row = await pool.fetchrow(
        """
        INSERT INTO procedure_submissions (
            id, goal_id, submission_type, parent_procedure_row_id, procedure_row_id, name, content,
            rationale, applicability_context, constraints, implementation_requirements, supporting_evidence,
            status, status_reason, layer1_result, layer2_result, embedding, duplicate_of_submission_id, duplicate_score,
            parent_similarity_score, submitted_by, provenance, scope_type, scope_entity_id, owner_id, visibility
        ) VALUES ($1,$2,$3,$4,NULL,$5,$6::jsonb,
                  $7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,
                  $12,$13,$14::jsonb,$15::jsonb,$16::vector,$17,$18,
                  $19,$20,$21,$22,$23,$24,$25)
        RETURNING *
        """,
        row_id,
        goal_id,
        submission_type,
        parent_procedure_row_id,
        name,
        json.dumps(content),
        rationale,
        json.dumps(applicability_context or {}),
        json.dumps(constraints or []),
        json.dumps(implementation_requirements or {}),
        json.dumps(submitted_evidence or []),
        status,
        status_reason,
        json.dumps(layer1),
        json.dumps(layer2),
        to_pgvector(embedding) if embedding else None,
        duplicate.get("best_match_id") if duplicate.get("best_match_kind") == "submission" else None,
        duplicate.get("score"),
        (parent_similarity or {}).get("score"),
        actor_subject,
        metadata["provenance"],
        metadata["scope_type"],
        metadata["scope_entity_id"],
        actor_subject,
        metadata["visibility"],
    )

    try:
        captured = await capture_procedure(
            pool,
            name=name,
            goal=goal.get("canonical_name") or goal.get("title") or name,
            steps=steps,
            preconditions=preconditions,
            expected_effects=expected_effects or [],
            postconditions=postconditions or [],
            invariants=constraints or [],
            failure_conditions=failure_conditions or [],
            provenance=metadata["provenance"],
            domain=metadata["scope_entity_id"],
            domain_payload={
                "implementation_requirements": dict(implementation_requirements or {}),
                "expected_outcome": expected_outcome,
            },
            family_id=parent_procedure_row_id,
            created_by=actor_subject,
            owner_id=actor_subject,
            visibility=metadata["visibility"],
            tenant_id=tenant_scope.tenant_id if tenant_scope is not None else None,
            embedding=embedding,
            scope_type=metadata["scope_type"],
            scope_entity_id=metadata["scope_entity_id"],
            display_name=name,
        )
    except Exception:
        await pool.execute(
            "UPDATE procedure_submissions SET status = 'needs_review', "
            "status_reason = COALESCE(status_reason, 'procedure capture failed') WHERE id = $1",
            row_id,
        )
        raise

    captured_procedure = await get_procedure(pool, str(captured["id"]))
    if captured_procedure is None or str(captured_procedure.get("achieves_goal_id")) != str(goal_id):
        await pool.execute(
            "UPDATE procedure_submissions SET status = 'needs_review', "
            "status_reason = COALESCE(status_reason, 'captured procedure does not achieve the submitted Goal') "
            "WHERE id = $1",
            row_id,
        )
        raise ValueError("captured procedure does not achieve the submitted Goal")

    updated = await pool.fetchrow(
        "UPDATE procedure_submissions SET procedure_row_id = $2 WHERE id = $1 RETURNING *",
        row_id,
        captured["id"],
    )
    if updated is None:
        raise ValueError("procedure submission disappeared before it could be linked")
    return dict(updated)


async def review_procedure_submission(
    pool: asyncpg.Pool,
    *,
    submission_id: str,
    decision: str,
    actor_subject: str,
    note: Optional[str] = None,
    access_scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    if decision not in ("accepted", "rejected", "needs_review"):
        raise ValueError("decision must be 'accepted', 'rejected' or 'needs_review'")
    submission = await get_procedure_submission(pool, submission_id, scope=access_scope)
    if submission is None:
        raise ValueError(f"procedure submission {submission_id} not found or not visible")
    if decision == "accepted" and not submission.get("procedure_row_id"):
        raise ValueError("cannot accept a submission whose procedure was never captured (procedure_row_id is null)")

    if decision == "accepted" and submission.get("procedure_row_id"):
        procedure = await get_procedure(pool, str(submission["procedure_row_id"]))
        if procedure is None or not _row_visible(procedure, access_scope):
            raise ValueError("the submitted procedure is missing or not visible to the reviewer")
        if str(procedure.get("achieves_goal_id")) != str(submission["goal_id"]):
            raise ValueError("the submitted procedure does not achieve the submission Goal")
        stable_procedure_id = str(procedure["procedure_id"])
        association = {
            "goal_id": str(submission["goal_id"]),
            "solution_type": "procedure",
            "target_id": stable_procedure_id,
            "version": int(procedure.get("version") or 1),
            "status": "active",
            "proposer": submission["submitted_by"],
            "provenance": submission.get("provenance") or SUBMISSION_PROVENANCE,
            "owner_id": submission["submitted_by"],
            "scope_type": submission.get("scope_type"),
            "scope_entity_id": submission.get("scope_entity_id"),
        }
        if tenant_scope is not None:
            association["tenant_scope"] = tenant_scope
        await associate_solution(pool, **association)
        if submission["submission_type"] == "new":
            await credits_service.reward_new_procedure(pool, submission=submission)
        else:
            await credits_service.reward_improvement(pool, submission=submission)

    row = await pool.fetchrow(
        """
        UPDATE procedure_submissions
        SET status = $2, status_reason = COALESCE($3, status_reason),
            reviewed_by = CASE WHEN $2 IN ('accepted','rejected') THEN $4 ELSE reviewed_by END,
            reviewed_at = CASE WHEN $2 IN ('accepted','rejected') THEN now() ELSE reviewed_at END
        WHERE id = $1
        RETURNING *
        """,
        submission_id,
        decision,
        note,
        actor_subject,
    )
    if row is None:
        raise ValueError(f"procedure submission {submission_id} not found or not visible")
    return dict(row)


async def get_procedure_submission(
    pool: asyncpg.Pool,
    submission_id: str,
    *,
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    del tenant_scope
    scope = _scope(scope)
    submission_sql, goal_sql, params = _visibility_pair(scope, first_index=2)
    row = await pool.fetchrow(
        f"""
        SELECT {_PROCEDURE_DETAIL_COLUMNS}
        FROM procedure_submissions s
        JOIN goals g ON g.id = s.goal_id
        WHERE s.id = $1 AND g.t_invalid IS NULL AND {submission_sql} AND {goal_sql}
        """,
        submission_id,
        *params,
    )
    return _detail_projection(row, _PROCEDURE_DETAIL_COLUMNS)


async def list_procedure_submissions(
    pool: asyncpg.Pool,
    *,
    scope: AccessScope = AccessScope.anonymous(),
    goal_id: Optional[str] = None,
    status: Optional[str] = None,
    submitted_by: Optional[str] = None,
    limit: int = 50,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    del tenant_scope
    scope = _scope(scope)
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("s.goal_id", goal_id), ("s.status", status), ("s.submitted_by", submitted_by)):
        if value is not None:
            params.append(value)
            clauses.append(f"{column} = ${len(params)}")
    first_index = len(params) + 1
    submission_sql, goal_sql, visibility_params = _visibility_pair(scope, first_index=first_index)
    params.extend(visibility_params)
    limit_index = len(params) + 1
    params.append(min(int(limit), 200))
    where = " AND ".join([*clauses, "g.t_invalid IS NULL", submission_sql, goal_sql])
    rows = await pool.fetch(
        f"""
        SELECT {_PROCEDURE_LIST_COLUMNS}
        FROM procedure_submissions s
        JOIN goals g ON g.id = s.goal_id
        WHERE {where}
        ORDER BY s.created_at DESC LIMIT ${limit_index}
        """,
        *params,
    )
    return [_list_projection(row, _PROCEDURE_LIST_COLUMNS) for row in rows]


async def list_procedure_usage_events(
    pool: asyncpg.Pool,
    *,
    procedure_row_id: str,
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    scope = _scope(scope)
    procedure_sql, procedure_params, next_index = scope_predicates(
        scope, tenant_scope or TenantScope.unrestricted(), alias="p", param_index=2
    )
    goal_sql, goal_params, _ = scope_predicates(
        scope, TenantScope.unrestricted(), alias="g", param_index=next_index
    )
    visibility_params = [*procedure_params, *goal_params]
    rows = await pool.fetch(
        f"""
        SELECT {_USAGE_COLUMNS}
        FROM procedure_usage_events u
        JOIN procedures p ON p.id = u.procedure_row_id
        JOIN goals g ON g.id = COALESCE(p.achieves_goal_id, u.goal_id)
        WHERE u.procedure_row_id = $1 AND p.t_invalid IS NULL AND g.t_invalid IS NULL
          AND {procedure_sql} AND {goal_sql}
        ORDER BY u.created_at DESC LIMIT ${2 + len(visibility_params)}
        """,
        procedure_row_id,
        *visibility_params,
        min(int(limit), 200),
    )
    allowed = (
        "id", "procedure_row_id", "procedure_id", "goal_id", "benchmark_id", "is_self_use",
        "outcome_state", "verification_layer", "context_key", "created_at",
    )
    return [{key: dict(row)[key] for key in allowed if key in dict(row)} for row in rows]


async def create_benchmark_submission(
    pool: asyncpg.Pool,
    *,
    goal_id: str,
    name: str,
    actor_subject: str,
    description: Optional[str] = None,
    success_criteria: Optional[dict] = None,
    failure_criteria: Optional[list] = None,
    scope_conditions: Optional[list] = None,
    invariants: Optional[list] = None,
    verification_method: Optional[dict] = None,
    environment_specification: Optional[dict] = None,
    comparison_policy: Optional[dict] = None,
    provenance: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    visibility: str = "public",
    access_scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
    embedder: Optional[Embedder] = None,
    llm_evaluator=None,
) -> dict[str, Any]:
    del provenance, scope_type, scope_entity_id, visibility
    name = _required_text(name, "name")
    description, success_criteria, failure_criteria, scope_conditions = _validate_benchmark_payload(
        description=description,
        success_criteria=success_criteria,
        failure_criteria=failure_criteria,
        scope_conditions=scope_conditions,
    )
    access_scope = _scope(access_scope)
    goal = await get_goal_for_product(pool, goal_id, scope=access_scope)
    if goal is None:
        raise ValueError(f"goal {goal_id} not found or not visible")
    metadata = _goal_submission_metadata(goal, access_scope)
    validate_provenance(metadata["provenance"])

    layer1 = verification_service.evaluate_layer1(
        name=name, steps=[verification_method or {}], rationale=description
    )
    dedup_text = " ".join(
        part for part in (name, description, json.dumps(success_criteria)) if part
    )
    embedding = await embed_submission_text(embedder, dedup_text)
    duplicate = await score_benchmark_duplicate(
        pool, goal_id=goal_id, embedding=embedding, scope=access_scope, tenant_scope=tenant_scope
    )
    layer2 = await verification_service.evaluate_layer2(
        submission={"name": name, "rationale": description, "goal": goal},
        duplicate=duplicate,
        layer1=layer1,
        llm_evaluator=llm_evaluator,
    )
    status = "candidate" if layer2["decision"] == "candidate" else "needs_review"
    status_reason = "; ".join(layer2.get("notes", [])) if status == "needs_review" else None

    conflicting_procedure = await pool.fetchval(
        "SELECT id FROM procedure_submissions WHERE goal_id = $1 AND submitted_by = $2 AND status <> 'rejected' LIMIT 1",
        goal_id,
        actor_subject,
    )
    if conflicting_procedure is not None:
        status = "needs_review"
        conflict_note = (
            f"submitter also has a Procedure submission ({conflicting_procedure}) for this same Goal "
            "-- review for independence before acceptance"
        )
        status_reason = f"{status_reason}; {conflict_note}" if status_reason else conflict_note

    row_id = str(uuid7())
    verification_payload = {
        **(verification_method or {}),
        "failure_criteria": failure_criteria,
        "scope_conditions": scope_conditions,
    }
    row = await pool.fetchrow(
        """
        INSERT INTO benchmark_submissions (
            id, goal_id, benchmark_id, name, description, success_criteria, invariants, verification_method,
            status, status_reason, layer1_result, layer2_result, embedding, duplicate_of_submission_id, duplicate_score,
            submitted_by, provenance, scope_type, scope_entity_id, owner_id, visibility
        ) VALUES ($1,$2,NULL,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10::jsonb,$11::jsonb,$12::vector,$13,$14,$15,$16,$17,$18,$19,$20)
        RETURNING *
        """,
        row_id,
        goal_id,
        name,
        description,
        json.dumps(success_criteria),
        json.dumps(invariants or []),
        json.dumps(verification_payload),
        status,
        status_reason,
        json.dumps(layer1),
        json.dumps(layer2),
        to_pgvector(embedding) if embedding else None,
        duplicate.get("best_match_id") if duplicate.get("best_match_kind") == "submission" else None,
        duplicate.get("score"),
        actor_subject,
        metadata["provenance"],
        metadata["scope_type"],
        metadata["scope_entity_id"],
        actor_subject,
        metadata["visibility"],
    )

    try:
        benchmark = await create_benchmark(
            pool,
            goal_id=goal_id,
            name=name,
            description=description,
            evaluation_protocol=dict(verification_method or {}),
            success_criteria=success_criteria,
            environment_specification={
                **(environment_specification or {}),
                "scope_conditions": scope_conditions,
            },
            comparison_policy={
                **(comparison_policy or {}),
                "failure_criteria": failure_criteria,
            },
            status="draft",
            provenance=metadata["provenance"],
            metadata={
                "invariants": invariants or [],
                "verification_method": verification_payload,
                "scope_conditions": scope_conditions,
                "failure_criteria": failure_criteria,
                "submitted_by": actor_subject,
            },
            tenant_scope=tenant_scope,
        )
    except Exception:
        await pool.execute(
            "UPDATE benchmark_submissions SET status = 'needs_review', "
            "status_reason = COALESCE(status_reason, 'benchmark capture failed') WHERE id = $1",
            row_id,
        )
        raise

    if benchmark.get("goal_id") is not None and str(benchmark["goal_id"]) != str(goal_id):
        await pool.execute(
            "UPDATE benchmark_submissions SET status = 'needs_review', "
            "status_reason = COALESCE(status_reason, 'captured benchmark does not achieve the submitted Goal') "
            "WHERE id = $1",
            row_id,
        )
        raise ValueError("captured benchmark does not achieve the submitted Goal")

    updated = await pool.fetchrow(
        "UPDATE benchmark_submissions SET benchmark_id = $2 WHERE id = $1 RETURNING *",
        row_id,
        benchmark["id"],
    )
    if updated is None:
        raise ValueError("benchmark submission disappeared before it could be linked")
    return dict(updated)


async def review_benchmark_submission(
    pool: asyncpg.Pool,
    *,
    submission_id: str,
    decision: str,
    actor_subject: str,
    note: Optional[str] = None,
    access_scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    del tenant_scope
    if decision not in ("accepted", "rejected", "needs_review"):
        raise ValueError("decision must be 'accepted', 'rejected' or 'needs_review'")
    submission = await get_benchmark_submission(pool, submission_id, scope=access_scope)
    if submission is None:
        raise ValueError(f"benchmark submission {submission_id} not found or not visible")
    if decision == "accepted" and not submission.get("benchmark_id"):
        raise ValueError("cannot accept a submission whose benchmark was never captured (benchmark_id is null)")

    if decision == "accepted" and submission.get("benchmark_id"):
        await pool.execute(
            "UPDATE benchmarks SET status = 'active' WHERE id = $1 AND goal_id = $2 AND status = 'draft'",
            submission["benchmark_id"],
            submission["goal_id"],
        )

    row = await pool.fetchrow(
        """
        UPDATE benchmark_submissions
        SET status = $2, status_reason = COALESCE($3, status_reason),
            reviewed_by = CASE WHEN $2 IN ('accepted','rejected') THEN $4 ELSE reviewed_by END,
            reviewed_at = CASE WHEN $2 IN ('accepted','rejected') THEN now() ELSE reviewed_at END
        WHERE id = $1
        RETURNING *
        """,
        submission_id,
        decision,
        note,
        actor_subject,
    )
    if row is None:
        raise ValueError(f"benchmark submission {submission_id} not found or not visible")
    return dict(row)


def _lifecycle_from_counts(evaluation_count: Any, pass_count: Any, fail_count: Any) -> dict[str, Any]:
    count = int(evaluation_count or 0)
    passed = int(pass_count or 0)
    failed = int(fail_count or 0)
    validated = passed > 0 and failed > 0
    return {
        "used": count > 0,
        "validated": validated,
        "evaluation_count": count,
        "distinguishes_outcomes": validated,
    }


async def _benchmark_lifecycle(pool: asyncpg.Pool, submission: dict[str, Any]) -> dict[str, Any]:
    if not submission.get("benchmark_id"):
        return _lifecycle_from_counts(0, 0, 0)
    row = await pool.fetchrow(
        "SELECT count(*) AS evaluation_count, "
        "count(*) FILTER (WHERE aggregate_result = 'pass') AS pass_count, "
        "count(*) FILTER (WHERE aggregate_result = 'fail') AS fail_count "
        "FROM evaluations WHERE benchmark_id = $1 AND status = 'completed'",
        submission["benchmark_id"],
    )
    if row is None:
        return _lifecycle_from_counts(0, 0, 0)
    return _lifecycle_from_counts(row["evaluation_count"], row["pass_count"], row["fail_count"])


async def get_benchmark_submission(
    pool: asyncpg.Pool,
    submission_id: str,
    *,
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    del tenant_scope
    scope = _scope(scope)
    submission_sql, goal_sql, params = _visibility_pair(scope, first_index=2)
    row = await pool.fetchrow(
        f"""
        SELECT {_BENCHMARK_DETAIL_COLUMNS}
        FROM benchmark_submissions s
        JOIN goals g ON g.id = s.goal_id
        WHERE s.id = $1 AND g.t_invalid IS NULL AND {submission_sql} AND {goal_sql}
        """,
        submission_id,
        *params,
    )
    submission = _detail_projection(row, _BENCHMARK_DETAIL_COLUMNS)
    if submission is None:
        return None
    submission["lifecycle"] = _lifecycle_from_counts(
        submission.pop("_evaluation_count", 0),
        submission.pop("_pass_count", 0),
        submission.pop("_fail_count", 0),
    )
    return submission


async def list_benchmark_submissions(
    pool: asyncpg.Pool,
    *,
    scope: AccessScope = AccessScope.anonymous(),
    goal_id: Optional[str] = None,
    status: Optional[str] = None,
    submitted_by: Optional[str] = None,
    limit: int = 50,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    del tenant_scope
    scope = _scope(scope)
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("s.goal_id", goal_id), ("s.status", status), ("s.submitted_by", submitted_by)):
        if value is not None:
            params.append(value)
            clauses.append(f"{column} = ${len(params)}")
    first_index = len(params) + 1
    submission_sql, goal_sql, visibility_params = _visibility_pair(scope, first_index=first_index)
    params.extend(visibility_params)
    limit_index = len(params) + 1
    params.append(min(int(limit), 200))
    where = " AND ".join([*clauses, "g.t_invalid IS NULL", submission_sql, goal_sql])
    rows = await pool.fetch(
        f"""
        SELECT {_BENCHMARK_LIST_COLUMNS}
        FROM benchmark_submissions s
        JOIN goals g ON g.id = s.goal_id
        WHERE {where}
        ORDER BY s.created_at DESC LIMIT ${limit_index}
        """,
        *params,
    )
    return [_list_projection(row, _BENCHMARK_LIST_COLUMNS) for row in rows]
