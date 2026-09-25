from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

from app.db.session import create_pool
from app.economy import submissions as service
from app.economy.verification import record_usage_event
from app.services.access import AccessScope, TenantScope
from app.services.goals import normalize_goal_name
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute("DELETE FROM credit_ledger_events WHERE contributor_id LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_usage_events WHERE executed_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM solutions WHERE goal_id IN (SELECT id FROM goals WHERE canonical_name LIKE $1)", f"[{prefix}%")
    await pool.execute("DELETE FROM benchmark_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM benchmarks WHERE goal_id IN (SELECT id FROM goals WHERE canonical_name LIKE $1)", f"[{prefix}%")
    await pool.execute("DELETE FROM procedures WHERE created_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"[{prefix}%")


async def _make_private_goal(pool, prefix: str, owner: str) -> str:
    canonical_name = f"[{prefix}] safely regenerate generated bindings"
    goal_id = str(uuid7())
    await pool.execute(
        "INSERT INTO goals (id, canonical_name, normalized_name, description, objective, "
        "expected_outcome, verification_requirement, status, provenance, visibility, owner_id, "
        "scope_type, scope_entity_id, created_by) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,'candidate',$8,$9::visibility_level,$10,$11,$12,$10)",
        goal_id,
        canonical_name,
        normalize_goal_name(canonical_name),
        "Regenerate clients without drift",
        "Bindings match the source schema",
        json.dumps({"bindings_match": True}),
        json.dumps({"check": "contract tests"}),
        "system_pending_review",
        "private",
        owner,
        "user",
        owner,
    )
    return goal_id


def _procedure_payload(goal_id: str) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "submission_type": "new",
        "name": "Regenerate generated bindings",
        "steps": [{"order": 1, "action": "run_generator", "goal": "synchronize bindings"}],
        "rationale": "The generator is deterministic after dependencies are pinned.",
        "preconditions": [
            {"subject": "python", "predicate": "version_gte", "value": "3.12"}
        ],
        "expected_outcome": {"bindings_match_schema": True},
        "expected_effects": [{"effect": "bindings_regenerated", "target": "client"}],
        "postconditions": [{"predicate": "generated_client_tests_pass"}],
        "failure_conditions": [
            {"when": "schema is stale", "description": "generator exits non-zero"}
        ],
        "implementation_requirements": {"tools": ["python", "uv"], "network": False},
        "existing_evidence": [{"kind": "run_log", "uri": "run://unverified"}],
        "previous_executions": [{"context": "linux", "outcome": "success"}],
        "known_failure_modes": [{"trigger": "dirty worktree", "symptom": "merge conflict"}],
        "constraints": [{"invariant": "generated files are never hand edited"}],
    }


def _benchmark_payload(goal_id: str) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "name": "Generated client compatibility",
        "description": "This benchmark checks compatibility rather than favoring a generator.",
        "success_criteria": {"all_contract_tests_pass": True},
        "failure_criteria": ["any required contract test fails"],
        "scope_conditions": ["same runtime and schema versions"],
        "invariants": ["fixtures are version pinned"],
        "verification_method": {"kind": "executable", "command": "pytest contract"},
        "environment_specification": {"os": "linux", "python": "3.12"},
        "comparison_policy": {"paired": True},
    }


def test_procedure_submission_contract_is_exact_scoped_and_unverified():
    async def _run():
        prefix = "submissionreq-procedure"
        owner = f"{prefix}-owner"
        other = f"{prefix}-other"
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, prefix)
            goal_id = await _make_private_goal(pool, prefix, owner)
            scope = AccessScope.for_user(owner)
            with pytest.raises(ValueError, match="not found or not visible"):
                await service.create_procedure_submission(
                    pool,
                    actor_subject=other,
                    access_scope=AccessScope.for_user(other),
                    tenant_scope=TenantScope.commons(),
                    **_procedure_payload(goal_id),
                )
            submission = await service.create_procedure_submission(
                pool,
                actor_subject=owner,
                access_scope=scope,
                tenant_scope=TenantScope.commons(),
                **_procedure_payload(goal_id),
            )
            procedure = await pool.fetchrow(
                "SELECT goal, achieves_goal_id, preconditions, expected_effects, postconditions, "
                "failure_conditions, domain_payload, evidence_refs, verification_state, visibility, "
                "scope_type, scope_entity_id FROM procedures WHERE id = $1",
                submission["procedure_row_id"],
            )
            assert procedure["goal"] == f"[{prefix}] safely regenerate generated bindings"
            assert str(procedure["achieves_goal_id"]) == goal_id
            assert _json(procedure["preconditions"]) == _procedure_payload(goal_id)["preconditions"]
            assert _json(procedure["expected_effects"]) == _procedure_payload(goal_id)["expected_effects"]
            assert _json(procedure["postconditions"]) == _procedure_payload(goal_id)["postconditions"]
            assert _json(procedure["failure_conditions"]) == _procedure_payload(goal_id)["failure_conditions"]
            assert _json(procedure["domain_payload"])["implementation_requirements"] == {"tools": ["python", "uv"], "network": False}
            assert _json(procedure["domain_payload"])["expected_outcome"] == {"bindings_match_schema": True}
            assert _json(procedure["evidence_refs"]) == []
            assert procedure["verification_state"] == "candidate"
            assert procedure["visibility"] == "private"
            assert procedure["scope_type"] == "user"
            assert procedure["scope_entity_id"] == owner

            raw = await pool.fetchrow(
                "SELECT content, supporting_evidence, provenance, scope_type, scope_entity_id, "
                "owner_id, visibility FROM procedure_submissions WHERE id = $1",
                submission["id"],
            )
            assert _json(raw["content"])["expected_outcome"] == {"bindings_match_schema": True}
            assert _json(raw["supporting_evidence"]) == [{"kind": "run_log", "uri": "run://unverified"}]
            assert raw["provenance"] == service.SUBMISSION_PROVENANCE
            assert raw["scope_type"] == "user"
            assert raw["scope_entity_id"] == owner
            assert raw["owner_id"] == owner
            assert raw["visibility"] == "private"

            accepted = await service.review_procedure_submission(
                pool,
                submission_id=submission["id"],
                decision="accepted",
                actor_subject=f"{prefix}-reviewer",
            )
            assert accepted["status"] == "accepted"
            stable_id = await pool.fetchval(
                "SELECT procedure_id FROM procedures WHERE id = $1",
                submission["procedure_row_id"],
            )
            solution = await pool.fetchrow(
                "SELECT target_id FROM solutions WHERE goal_id = $1 AND target_table = 'procedures'",
                goal_id,
            )
            assert str(solution["target_id"]) == str(stable_id)
            assert str(solution["target_id"]) != str(submission["procedure_row_id"])
            assert await pool.fetchval(
                "SELECT verification_state FROM procedures WHERE id = $1",
                submission["procedure_row_id"],
            ) == "candidate"
            assert await pool.fetchval(
                "SELECT count(*) FROM evidence WHERE target_type = 'procedure' AND target_id = $1",
                submission["procedure_row_id"],
            ) == 0

            usage_event = await record_usage_event(
                pool,
                procedure_row_id=submission["procedure_row_id"],
                executor_subject=owner,
                context_key="ctx",
            )
            assert str(usage_event["goal_id"]) == goal_id
            assert usage_event["outcome_state"] == "unknown"
            owner_events = await service.list_procedure_usage_events(
                pool,
                procedure_row_id=submission["procedure_row_id"],
                scope=scope,
                tenant_scope=TenantScope.commons(),
            )
            denied_events = await service.list_procedure_usage_events(
                pool,
                procedure_row_id=submission["procedure_row_id"],
                scope=AccessScope.for_user(other),
                tenant_scope=TenantScope.commons(),
            )
            assert len(owner_events) == 1
            assert "metadata" not in owner_events[0]
            assert denied_events == []
            assert await service.get_procedure_submission(
                pool, submission["id"], scope=scope,
            ) is not None
            assert await service.get_procedure_submission(
                pool, submission["id"], scope=AccessScope.for_user(other),
            ) is None
            assert [item["id"] for item in await service.list_procedure_submissions(
                pool, scope=scope,
            )] == [submission["id"]]
            assert await service.list_procedure_submissions(
                pool, scope=AccessScope.for_user(other),
            ) == []
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_benchmark_submission_reuses_criteria_columns_and_scopes_reads():
    async def _run():
        prefix = "submissionreq-benchmark"
        owner = f"{prefix}-owner"
        other = f"{prefix}-other"
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, prefix)
            goal_id = await _make_private_goal(pool, prefix, owner)
            payload = _benchmark_payload(goal_id)
            submission = await service.create_benchmark_submission(
                pool,
                actor_subject=owner,
                access_scope=AccessScope.for_user(owner),
                tenant_scope=TenantScope.commons(),
                **payload,
            )
            benchmark = await pool.fetchrow(
                "SELECT description, success_criteria, environment_specification, comparison_policy, "
                "metadata, evaluation_protocol FROM benchmarks WHERE id = $1",
                submission["benchmark_id"],
            )
            assert benchmark["description"] == payload["description"]
            assert _json(benchmark["success_criteria"]) == payload["success_criteria"]
            assert _json(benchmark["environment_specification"])["scope_conditions"] == payload["scope_conditions"]
            assert _json(benchmark["comparison_policy"])["failure_criteria"] == payload["failure_criteria"]
            assert _json(benchmark["metadata"])["failure_criteria"] == payload["failure_criteria"]
            assert _json(benchmark["metadata"])["scope_conditions"] == payload["scope_conditions"]
            assert _json(benchmark["evaluation_protocol"])["kind"] == "executable"

            raw = await pool.fetchrow(
                "SELECT description, success_criteria, verification_method, provenance, scope_type, "
                "scope_entity_id, owner_id, visibility FROM benchmark_submissions WHERE id = $1",
                submission["id"],
            )
            assert raw["description"] == payload["description"]
            assert _json(raw["success_criteria"]) == payload["success_criteria"]
            assert _json(raw["verification_method"])["failure_criteria"] == payload["failure_criteria"]
            assert _json(raw["verification_method"])["scope_conditions"] == payload["scope_conditions"]
            assert raw["provenance"] == service.SUBMISSION_PROVENANCE
            assert raw["scope_type"] == "user"
            assert raw["scope_entity_id"] == owner
            assert raw["owner_id"] == owner
            assert raw["visibility"] == "private"

            assert await service.get_benchmark_submission(
                pool, submission["id"], scope=AccessScope.for_user(owner),
            ) is not None
            assert await service.get_benchmark_submission(
                pool, submission["id"], scope=AccessScope.for_user(other),
            ) is None
            # scoped to this submitter: other tests' PUBLIC submissions in the same
            # database are visible to everyone and are not what this checks
            assert len(await service.list_benchmark_submissions(
                pool, scope=AccessScope.for_user(owner), submitted_by=owner,
            )) == 1
            assert await service.list_benchmark_submissions(
                pool, scope=AccessScope.for_user(other), submitted_by=owner,
            ) == []
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
