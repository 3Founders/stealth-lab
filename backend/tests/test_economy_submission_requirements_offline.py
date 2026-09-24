from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.economy import submissions as service
from app.services.access import AccessScope, TenantScope

ACTOR = "submission-owner"
GOAL_ID = "00000000-0000-4000-8000-000000000001"
ROW_ID = "00000000-0000-4000-8000-000000000002"
PROCEDURE_STABLE_ID = "00000000-0000-4000-8000-000000000003"
BENCHMARK_ID = "00000000-0000-4000-8000-000000000004"


def _goal() -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "canonical_name": "Safely regenerate generated API bindings",
        "description": "Keep generated clients synchronized",
        "objective": "Regenerate without drift",
        "expected_outcome": {"bindings_match": True},
        "verification_requirement": {"check": "generated-client tests"},
        "visibility": "public",
        "owner_id": None,
        "scope_type": "global",
        "scope_entity_id": None,
    }


def _procedure_payload() -> dict[str, Any]:
    return {
        "goal_id": GOAL_ID,
        "submission_type": "new",
        "name": "Regenerate bindings safely",
        "steps": [{"order": 1, "action": "run_generator"}],
        "rationale": "The generator is deterministic after dependency installation.",
        "preconditions": [
            {"subject": "python", "predicate": "version_gte", "value": "3.12"}
        ],
        "expected_outcome": {"bindings_match_schema": True},
        "expected_effects": [{"effect": "bindings_regenerated", "target": "client"}],
        "postconditions": [{"predicate": "generated_client_tests_pass"}],
        "failure_conditions": [{"when": "schema is stale", "description": "generator exits non-zero"}],
        "implementation_requirements": {"tools": ["python", "uv"], "network": False},
        "existing_evidence": [{"kind": "run_log", "uri": "run://123"}],
        "previous_executions": [{"context": "linux", "outcome": "success"}],
        "known_failure_modes": [{"trigger": "dirty worktree", "symptom": "merge conflict"}],
        "constraints": [{"invariant": "do_not_edit_generated_files"}],
    }


def _benchmark_payload() -> dict[str, Any]:
    return {
        "goal_id": GOAL_ID,
        "name": "Generated client compatibility",
        "description": "This checks compatibility rather than preferring one generator.",
        "success_criteria": {"all_contract_tests_pass": True},
        "failure_criteria": ["any required contract test fails"],
        "scope_conditions": ["same runtime and schema versions"],
        "invariants": ["fixtures are version-pinned"],
        "verification_method": {"kind": "executable", "command": "pytest contract"},
        "environment_specification": {"os": "linux", "python": "3.12"},
        "comparison_policy": {"paired": True},
    }


class WritePool:
    def __init__(self, *, achieved_goal_id: str = GOAL_ID) -> None:
        self.achieved_goal_id = achieved_goal_id
        self.procedure_content: dict[str, Any] | None = None
        self.procedure_insert: dict[str, Any] | None = None
        self.benchmark_insert: dict[str, Any] | None = None
        self.linked_procedure = False
        self.linked_benchmark = False
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, _sql: str, *_args: Any):
        return None

    async def fetchrow(self, sql: str, *args: Any):
        compact = " ".join(sql.split())
        if compact.startswith("INSERT INTO procedure_submissions"):
            self.procedure_content = json.loads(args[5])
            self.procedure_insert = {
                "id": args[0], "goal_id": args[1], "submitted_by": args[19],
                "provenance": args[20], "scope_type": args[21],
                "scope_entity_id": args[22], "owner_id": args[23], "visibility": args[24],
                "status": args[11], "content": self.procedure_content,
            }
            return self.procedure_insert
        if compact.startswith("INSERT INTO benchmark_submissions"):
            self.benchmark_insert = {
                "id": args[0], "goal_id": args[1], "benchmark_id": None,
                "name": args[2], "description": args[3], "success_criteria": json.loads(args[4]),
                "invariants": json.loads(args[5]), "verification_method": json.loads(args[6]),
                "status": args[7], "submitted_by": args[14], "provenance": args[15],
                "scope_type": args[16], "scope_entity_id": args[17], "owner_id": args[18],
                "visibility": args[19],
            }
            return self.benchmark_insert
        if compact.startswith("SELECT id, procedure_id, achieves_goal_id FROM procedures"):
            return {
                "id": args[0], "procedure_id": PROCEDURE_STABLE_ID,
                "achieves_goal_id": self.achieved_goal_id,
            }
        if compact.startswith("UPDATE procedure_submissions SET procedure_row_id"):
            self.linked_procedure = True
            result = dict(self.procedure_insert or {})
            result["procedure_row_id"] = args[1]
            return result
        if compact.startswith("UPDATE benchmark_submissions SET benchmark_id"):
            self.linked_benchmark = True
            result = dict(self.benchmark_insert or {})
            result["benchmark_id"] = args[1]
            return result
        raise AssertionError(f"unexpected fetchrow: {compact}")

    async def execute(self, sql: str, *args: Any):
        self.executions.append((" ".join(sql.split()), args))
        return "UPDATE 1"


async def _run_captured_procedure(pool, procedure_id):
    return await pool.fetchrow(
        "SELECT id, procedure_id, achieves_goal_id FROM procedures WHERE id = $1",
        procedure_id,
    )


def _patch_common(monkeypatch) -> None:
    async def get_goal(_pool, goal_id, *, scope):
        assert goal_id == GOAL_ID
        assert scope.viewer_id == ACTOR
        return _goal()

    async def embed(_embedder, _text):
        return None

    async def procedure_duplicate(*_args, **_kwargs):
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": []}

    async def benchmark_duplicate(*_args, **_kwargs):
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": []}

    monkeypatch.setattr(service, "get_goal_for_product", get_goal)
    monkeypatch.setattr(
        service,
        "get_procedure",
        lambda _pool, procedure_id: _run_captured_procedure(_pool, procedure_id),
    )
    monkeypatch.setattr(service, "embed_submission_text", embed)
    monkeypatch.setattr(service, "score_procedure_duplicate", procedure_duplicate)
    monkeypatch.setattr(service, "score_benchmark_duplicate", benchmark_duplicate)


def test_procedure_requirements_are_server_validated_before_any_write():
    base = _procedure_payload()
    variants = []
    for field in ("rationale", "preconditions", "expected_outcome"):
        payload = dict(base)
        payload.pop(field)
        variants.append(payload)
    rationale = dict(base)
    rationale["rationale"] = "   "
    variants.append(rationale)
    malformed = dict(base)
    malformed["preconditions"] = [{"subject": "python", "predicate": "available"}]
    variants.append(malformed)
    empty_outcome = dict(base)
    empty_outcome["expected_outcome"] = {}
    variants.append(empty_outcome)

    for payload in variants:
        with pytest.raises(ValueError):
            asyncio.run(
                service.create_procedure_submission(
                    None,
                    actor_subject=ACTOR,
                    access_scope=AccessScope.for_user(ACTOR),
                    tenant_scope=TenantScope.commons(),
                    **payload,
                )
            )


def test_benchmark_requirements_are_server_validated_before_any_write():
    base = _benchmark_payload()
    for field in ("description", "success_criteria", "failure_criteria", "scope_conditions"):
        payload = dict(base)
        payload.pop(field)
        with pytest.raises(ValueError):
            asyncio.run(
                service.create_benchmark_submission(
                    None,
                    actor_subject=ACTOR,
                    access_scope=AccessScope.for_user(ACTOR),
                    tenant_scope=TenantScope.commons(),
                    **payload,
                )
            )


def test_procedure_submission_uses_exact_goal_and_propagates_quality_fields(monkeypatch):
    _patch_common(monkeypatch)
    pool = WritePool()
    captured: dict[str, Any] = {}

    async def capture(_pool, **kwargs):
        captured.update(kwargs)
        return {"id": ROW_ID, "procedure_id": PROCEDURE_STABLE_ID}

    monkeypatch.setattr(service, "capture_procedure", capture)
    payload = _procedure_payload()
    result = asyncio.run(
        service.create_procedure_submission(
            pool,
            actor_subject=ACTOR,
            access_scope=AccessScope.for_user(ACTOR),
            tenant_scope=TenantScope.commons(),
            **payload,
        )
    )

    assert result["procedure_row_id"] == ROW_ID
    assert captured["goal"] == _goal()["canonical_name"]
    assert captured["scope_type"] == "global"
    assert captured["scope_entity_id"] is None
    assert captured["visibility"] == "public"
    assert captured["provenance"] == service.SUBMISSION_PROVENANCE
    assert captured["preconditions"] == payload["preconditions"]
    assert captured["expected_effects"] == payload["expected_effects"]
    assert captured["postconditions"] == payload["postconditions"]
    assert captured["failure_conditions"] == payload["failure_conditions"]
    assert captured["domain_payload"]["implementation_requirements"] == payload["implementation_requirements"]
    assert captured["domain_payload"]["expected_outcome"] == payload["expected_outcome"]
    assert "evidence_refs" not in captured
    assert pool.procedure_content == {
        "steps": payload["steps"],
        "preconditions": payload["preconditions"],
        "expected_outcome": payload["expected_outcome"],
        "expected_effects": payload["expected_effects"],
        "postconditions": payload["postconditions"],
        "failure_conditions": payload["failure_conditions"],
        "implementation_requirements": payload["implementation_requirements"],
        "previous_executions": payload["previous_executions"],
        "known_failure_modes": payload["known_failure_modes"],
    }
    assert pool.procedure_insert["provenance"] == service.SUBMISSION_PROVENANCE
    assert pool.procedure_insert["scope_type"] == "global"
    assert pool.procedure_insert["scope_entity_id"] is None
    assert pool.procedure_insert["visibility"] == "public"


def test_wrong_captured_goal_is_rejected_and_submission_is_needs_review(monkeypatch):
    _patch_common(monkeypatch)
    pool = WritePool(achieved_goal_id="00000000-0000-4000-8000-000000000099")

    async def capture(_pool, **_kwargs):
        return {"id": ROW_ID, "procedure_id": PROCEDURE_STABLE_ID}

    monkeypatch.setattr(service, "capture_procedure", capture)
    with pytest.raises(ValueError, match="does not achieve"):
        asyncio.run(
            service.create_procedure_submission(
                pool,
                actor_subject=ACTOR,
                access_scope=AccessScope.for_user(ACTOR),
                tenant_scope=TenantScope.commons(),
                **_procedure_payload(),
            )
        )
    assert pool.linked_procedure is False
    assert any("does not achieve" in sql for sql, _args in pool.executions)


def test_benchmark_criteria_reuse_existing_benchmark_columns(monkeypatch):
    _patch_common(monkeypatch)
    pool = WritePool()
    created: dict[str, Any] = {}

    async def create_benchmark(_pool, **kwargs):
        created.update(kwargs)
        return {"id": BENCHMARK_ID}

    monkeypatch.setattr(service, "create_benchmark", create_benchmark)
    payload = _benchmark_payload()
    result = asyncio.run(
        service.create_benchmark_submission(
            pool,
            actor_subject=ACTOR,
            access_scope=AccessScope.for_user(ACTOR),
            tenant_scope=TenantScope.commons(),
            **payload,
        )
    )

    assert result["benchmark_id"] == BENCHMARK_ID
    assert created["success_criteria"] == payload["success_criteria"]
    assert created["environment_specification"]["scope_conditions"] == payload["scope_conditions"]
    assert created["comparison_policy"]["failure_criteria"] == payload["failure_criteria"]
    assert created["metadata"]["verification_method"]["kind"] == "executable"
    assert created["metadata"]["verification_method"]["scope_conditions"] == payload["scope_conditions"]
    assert created["metadata"]["failure_criteria"] == payload["failure_criteria"]
    assert created["provenance"] == service.SUBMISSION_PROVENANCE
    assert pool.benchmark_insert["verification_method"]["failure_criteria"] == payload["failure_criteria"]
    assert pool.benchmark_insert["scope_type"] == "global"
    assert pool.benchmark_insert["scope_entity_id"] is None
    assert pool.benchmark_insert["visibility"] == "public"


def test_review_associates_stable_procedure_id_not_version_row(monkeypatch):
    submission = {
        "id": "submission-1",
        "goal_id": GOAL_ID,
        "procedure_row_id": ROW_ID,
        "submission_type": "new",
        "submitted_by": ACTOR,
        "provenance": service.SUBMISSION_PROVENANCE,
        "scope_type": "user",
        "scope_entity_id": ACTOR,
    }
    associated: dict[str, Any] = {}

    async def get_submission(_pool, _submission_id, *, scope):
        assert isinstance(scope, AccessScope)
        return submission

    async def associate(_pool, **kwargs):
        associated.update(kwargs)

    async def reward(_pool, *, submission):
        assert submission["id"] == "submission-1"

    class ReviewPool:
        async def fetchrow(self, sql: str, *args: Any):
            compact = " ".join(sql.split())
            if compact.startswith("SELECT id, procedure_id, achieves_goal_id FROM procedures"):
                return {"id": args[0], "procedure_id": PROCEDURE_STABLE_ID, "achieves_goal_id": GOAL_ID}
            if compact.startswith("UPDATE procedure_submissions"):
                return {**submission, "status": "accepted"}
            raise AssertionError(compact)

    async def get_procedure(_pool, procedure_row_id):
        return {
            "id": procedure_row_id,
            "procedure_id": PROCEDURE_STABLE_ID,
            "achieves_goal_id": GOAL_ID,
        }

    monkeypatch.setattr(service, "get_procedure_submission", get_submission)
    monkeypatch.setattr(service, "get_procedure", get_procedure)
    monkeypatch.setattr(service, "associate_solution", associate)
    monkeypatch.setattr(service.credits_service, "reward_new_procedure", reward)
    result = asyncio.run(
        service.review_procedure_submission(
            ReviewPool(), submission_id="submission-1", decision="accepted",
            actor_subject="reviewer", access_scope=AccessScope.for_user("reviewer"),
        )
    )
    assert result["status"] == "accepted"
    assert associated["target_id"] == PROCEDURE_STABLE_ID
    assert associated["target_id"] != ROW_ID


class ReadPool:
    def __init__(self) -> None:
        self.fetchrow_queries: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any):
        self.fetchrow_queries.append((" ".join(sql.split()), args))
        return {
            "id": "submission-1", "goal_id": GOAL_ID, "name": "safe", "status": "candidate",
            "content": {"secret": "raw"}, "supporting_evidence": [{"secret": "raw"}],
            "embedding": "private-vector", "submitted_by": ACTOR, "owner_id": ACTOR,
            "visibility": "private", "created_at": None, "updated_at": None,
        }

    async def fetch(self, sql: str, *args: Any):
        self.fetch_queries.append((" ".join(sql.split()), args))
        if "procedure_usage_events" in sql:
            return [{
                "id": "usage-1", "procedure_row_id": ROW_ID, "procedure_id": PROCEDURE_STABLE_ID,
                "goal_id": GOAL_ID, "benchmark_id": None, "is_self_use": False,
                "outcome_state": "unknown", "verification_layer": 0, "context_key": "ctx",
                "created_at": None, "executed_by": "private-user", "contributor_id": ACTOR,
                "metadata": {"secret": "raw"}, "evidence_id": "private-evidence",
                "execution_run_id": "private-run",
            }]
        return [{
            "id": "submission-1", "goal_id": GOAL_ID, "name": "safe", "status": "candidate",
            "submitted_by": ACTOR, "owner_id": ACTOR, "visibility": "private",
            "created_at": None, "updated_at": None, "embedding": "private-vector",
        }]


def test_procedure_review_refuses_cross_user_before_state_or_reward_changes():
    class ReviewReadPool:
        def __init__(self):
            self.queries = []

        async def fetchrow(self, sql, *args):
            self.queries.append((" ".join(sql.split()), args))

    pool = ReviewReadPool()
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(service.review_procedure_submission(
            pool,
            submission_id="private-submission",
            decision="accepted",
            actor_subject="reviewer",
            access_scope=AccessScope.for_user("other-user"),
        ))

    sql, args = pool.queries[0]
    assert "JOIN goals g" in sql
    assert "g.visibility = 'public'" in sql
    assert "other-user" in args


def test_benchmark_review_refuses_cross_user_before_state_changes():
    class ReviewReadPool:
        def __init__(self):
            self.queries = []

        async def fetchrow(self, sql, *args):
            self.queries.append((" ".join(sql.split()), args))

    pool = ReviewReadPool()
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(service.review_benchmark_submission(
            pool,
            submission_id="private-submission",
            decision="accepted",
            actor_subject="reviewer",
            access_scope=AccessScope.for_user("other-user"),
        ))

    sql, args = pool.queries[0]
    assert "JOIN goals g" in sql
    assert "g.visibility = 'public'" in sql
    assert "other-user" in args


def test_submission_reads_are_scoped_and_use_safe_projections():
    pool = ReadPool()
    scope = AccessScope.for_user(ACTOR)
    detail = asyncio.run(service.get_procedure_submission(pool, "submission-1", scope=scope))
    listed = asyncio.run(service.list_procedure_submissions(pool, scope=scope))
    usage = asyncio.run(
        service.list_procedure_usage_events(
            pool, procedure_row_id=ROW_ID, scope=scope, tenant_scope=TenantScope.commons(),
        )
    )
    benchmark_detail = asyncio.run(
        service.get_benchmark_submission(pool, "benchmark-submission-1", scope=scope)
    )
    benchmark_list = asyncio.run(service.list_benchmark_submissions(pool, scope=scope))

    detail_sql, detail_args = pool.fetchrow_queries[0]
    list_sql, list_args = pool.fetch_queries[0]
    usage_sql, _usage_args = pool.fetch_queries[1]
    assert "s.visibility = 'public'" in detail_sql
    assert "g.visibility = 'public'" in detail_sql
    assert detail_args == ("submission-1", ACTOR, ACTOR)
    assert list_args == (ACTOR, ACTOR, 50)
    assert "s.visibility = 'public'" in list_sql
    assert "g.visibility = 'public'" in list_sql
    assert "embedding" not in detail_sql
    assert "content" not in list_sql
    assert "embedding" not in list_sql
    assert "embedding" not in detail
    assert "embedding" not in listed[0]
    assert "content" not in listed[0]
    assert "metadata" not in usage_sql
    assert "executed_by" not in usage_sql
    assert "contributor_id" not in usage_sql
    assert "evidence_id" not in usage_sql
    assert "execution_run_id" not in usage_sql
    assert "metadata" not in usage[0]
    assert "executed_by" not in usage[0]
    assert "contributor_id" not in usage[0]
    assert "evidence_id" not in usage[0]
    assert "execution_run_id" not in usage[0]
    benchmark_detail_sql, benchmark_detail_args = pool.fetchrow_queries[1]
    benchmark_list_sql, _benchmark_list_args = pool.fetch_queries[2]
    assert "s.visibility = 'public'" in benchmark_detail_sql
    assert "g.visibility = 'public'" in benchmark_detail_sql
    assert benchmark_detail_args == ("benchmark-submission-1", ACTOR, ACTOR)
    assert "s.visibility = 'public'" in benchmark_list_sql
    assert "g.visibility = 'public'" in benchmark_list_sql
    assert "embedding" not in benchmark_detail_sql
    assert "embedding" not in benchmark_list_sql
    assert "embedding" not in benchmark_detail
    assert "embedding" not in benchmark_list[0]
    assert "content" not in benchmark_list[0]
