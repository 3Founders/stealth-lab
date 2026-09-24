from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import benchmark_transfer as service
from app.services.access import AccessScope
from app.services.semantic.errors import SemanticJudgmentUnavailable

G_SPECIFIC = "00000000-0000-4000-8000-000000000101"
G_ABSTRACT = "00000000-0000-4000-8000-000000000102"
G_REJECTED = "00000000-0000-4000-8000-000000000103"
B_SPECIFIC = "00000000-0000-4000-8000-000000000201"
B_ABSTRACT = "00000000-0000-4000-8000-000000000202"
TENANT = "00000000-0000-0000-0000-000000000001"


def _goal(goal_id: str, name: str, *, owner_id=None, visibility="public", scope_type="global", scope_entity_id=None):
    return {
        "id": goal_id,
        "canonical_name": name,
        "description": f"{name} description",
        "objective": f"complete {name}",
        "constraints": ["bounded"],
        "expected_outcome": {"done": True},
        "verification_requirement": {"kind": "review"},
        "version": 1,
        "status": "active",
        "t_invalid": None,
        "scope_type": scope_type,
        "scope_entity_id": scope_entity_id,
        "visibility": visibility,
        "owner_id": owner_id,
        "home_shard_id": "K000",
    }


def _benchmark(benchmark_id: str, goal_id: str, name: str, *, status="frozen", frozen_at="2026-01-01T00:00:00+00:00"):
    return {
        "id": benchmark_id,
        "goal_id": goal_id,
        "name": name,
        "description": f"{name} measures a stable contract",
        "version": 1,
        "evaluation_protocol": {"runner": "contract-tests"},
        "environment_specification": {"os": "linux"},
        "success_criteria": {"contract": "passes"},
        "comparison_policy": {"paired": True},
        "status": status,
        "frozen_at": frozen_at,
        "provenance": "prior_library",
        "metadata": {
            "invariants": ["fixtures are pinned"],
            "secret_evidence": "must not transfer",
            "verification_state": "verified",
        },
    }



def _decoded_jsonb(value):
    # The real pool's jsonb codec takes Python values; a pre-serialized string
    # would be stored as a JSON string rather than an object.
    assert not isinstance(value, str), "jsonb parameter must not be pre-serialized"
    return value

class FakePool:
    def __init__(self):
        self.goals = {}
        self.benchmarks = {}
        self.submissions = {}
        self.lineage = {}
        self.jobs = []
        self.relations = []
        self.writes = []
        self.transaction_count = 0
        self.next_id = 1

    def add_goal(self, goal):
        self.goals[str(goal["id"])] = dict(goal)

    def add_benchmark(self, benchmark):
        self.benchmarks[str(benchmark["id"])] = dict(benchmark)

    def add_relation(self, specific, abstract, status="accepted"):
        self.relations.append({
            "specific_goal_id": specific,
            "abstract_goal_id": abstract,
            "relation_type": "SPECIALIZES",
            "status": status,
            "confidence": 0.91,
            "provenance": "accepted_edge",
            "decision_id": "00000000-0000-4000-8000-000000000301",
            "decision_metadata": {"policy": "goal_abstraction_auto_acceptance"},
            "decided_by": None,
            "decided_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "scope_type": "global",
            "scope_entity_id": None,
            "tenant_id": "00000000-0000-0000-0000-000000000001",
        })

    def _visible(self, sql, args, goal):
        compact = " ".join(sql.split())
        if "(TRUE) AND (TRUE)" in compact:
            return True
        if "g.owner_id = $2" in compact:
            return goal.get("owner_id") == args[1]
        if "g.visibility = 'public'" in compact:
            return goal.get("visibility") == "public"
        return True

    def _source_row(self, benchmark_id):
        benchmark = self.benchmarks[str(benchmark_id)]
        goal = self.goals[str(benchmark["goal_id"])]
        return {
            "benchmark_id": benchmark["id"],
            "source_goal_id": benchmark["goal_id"],
            "joined_goal_id": goal["id"],
            "benchmark_name": benchmark["name"],
            "benchmark_description": benchmark["description"],
            "benchmark_version": benchmark["version"],
            "benchmark_evaluation_protocol": benchmark["evaluation_protocol"],
            "benchmark_environment_specification": benchmark["environment_specification"],
            "benchmark_success_criteria": benchmark["success_criteria"],
            "benchmark_comparison_policy": benchmark["comparison_policy"],
            "benchmark_status": benchmark["status"],
            "benchmark_frozen_at": benchmark["frozen_at"],
            "benchmark_provenance": benchmark["provenance"],
            "benchmark_metadata": benchmark["metadata"],
            "goal_name": goal["canonical_name"],
            "goal_description": goal["description"],
            "goal_objective": goal["objective"],
            "goal_constraints": goal["constraints"],
            "goal_expected_outcome": goal["expected_outcome"],
            "goal_verification_requirement": goal["verification_requirement"],
            "goal_version": goal["version"],
            "goal_status": goal["status"],
            "goal_t_invalid": goal["t_invalid"],
            "goal_scope_type": goal["scope_type"],
            "goal_scope_entity_id": goal["scope_entity_id"],
            "goal_visibility": goal["visibility"],
            "goal_owner_id": goal["owner_id"],
        }

    def _target_row(self, goal_id):
        goal = self.goals[str(goal_id)]
        return {
            "target_goal_id": goal["id"],
            "target_name": goal["canonical_name"],
            "target_description": goal["description"],
            "target_objective": goal["objective"],
            "target_constraints": goal["constraints"],
            "target_expected_outcome": goal["expected_outcome"],
            "target_verification_requirement": goal["verification_requirement"],
            "target_version": goal["version"],
            "goal_status": goal["status"],
            "goal_t_invalid": goal["t_invalid"],
            "target_scope_type": goal["scope_type"],
            "target_scope_entity_id": goal["scope_entity_id"],
            "target_visibility": goal["visibility"],
            "target_owner_id": goal["owner_id"],
        }

    def _canonical_goal_row(self, goal_id):
        goal = self.goals[str(goal_id)]
        return {
            "id": goal["id"],
            "canonical_name": goal["canonical_name"],
            "description": goal["description"],
            "objective": goal["objective"],
            "constraints": goal["constraints"],
            "expected_outcome": goal["expected_outcome"],
            "verification_requirement": goal["verification_requirement"],
            "version": goal["version"],
            "status": goal["status"],
            "t_invalid": goal["t_invalid"],
            "scope_type": goal["scope_type"],
            "scope_entity_id": goal["scope_entity_id"],
            "visibility": goal["visibility"],
            "owner_id": goal["owner_id"],
            "home_shard_id": goal.get("home_shard_id", "K000"),
        }

    def _relation(self, source, target, accepted_only=True):
        for relation in self.relations:
            if accepted_only and relation["status"] != "accepted":
                continue
            if {relation["specific_goal_id"], relation["abstract_goal_id"]} == {str(source), str(target)}:
                return dict(relation)
        return None

    async def execute(self, sql, *args):
        compact = " ".join(sql.split())
        if compact == "SELECT pg_advisory_xact_lock(hashtext($1))":
            self.writes.append(("advisory_lock", args[0]))
            return "SELECT 1"
        if compact.startswith("SELECT set_config('app.benchmark_transfer_compensation'"):
            return "SELECT 1"
        if compact.startswith("DELETE FROM benchmark_transfer_decisions"):
            for key, row in list(self.lineage.items()):
                if str(row.get("id")) == str(args[0]):
                    self.lineage.pop(key, None)
            self.writes.append(("delete_lineage", str(args[0])))
            return "DELETE 1"
        if compact.startswith("DELETE FROM benchmark_submissions"):
            self.submissions.pop(str(args[0]), None)
            self.writes.append(("delete_submission", str(args[0])))
            return "DELETE 1"
        if compact.startswith("DELETE FROM benchmarks"):
            self.benchmarks.pop(str(args[0]), None)
            self.writes.append(("delete_benchmark", str(args[0])))
            return "DELETE 1"
        raise AssertionError(f"unexpected execute: {compact[:180]}")

    async def fetchrow(self, sql, *args):
        compact = " ".join(sql.split())
        if compact.startswith("INSERT INTO ingestion_jobs"):
            key = args[2]
            for job in self.jobs:
                if job["job_type"] == args[0] and job["idempotency_key"] == key:
                    return None
            job = {
                "id": self.next_id,
                "job_type": args[0],
                "payload": args[1],
                "idempotency_key": key,
                "source_id": args[3],
                "scope_type": args[4],
                "scope_entity_id": args[5],
                "owner_id": args[6],
                "visibility": args[7],
                "config_version": args[8],
                "max_attempts": args[9],
            }
            self.next_id += 1
            self.jobs.append(job)
            return job
        if "FROM goal_search_index" in compact and "FOR SHARE" in compact:
            goal = self.goals.get(str(args[0]))
            if goal is None:
                return None
            return {
                "id": goal["id"],
                "projected_status": goal["status"],
                "projected_version": goal["version"],
                "projected_scope_type": goal["scope_type"],
                "projected_scope_entity_id": goal["scope_entity_id"],
                "projected_visibility": goal["visibility"],
                "projected_owner_id": goal["owner_id"],
                "home_shard_id": goal.get("home_shard_id", "K000"),
                "tenant_id": TENANT,
            }
        if "FROM goal_search_index" in compact:
            goal = self.goals.get(str(args[0]))
            if goal is None or not self._visible(sql, args, goal):
                return None
            return {
                "id": goal["id"],
                "canonical_name": goal["canonical_name"],
                "projected_status": goal["status"],
                "projected_version": goal["version"],
                "projected_scope_type": goal["scope_type"],
                "projected_scope_entity_id": goal["scope_entity_id"],
                "projected_visibility": goal["visibility"],
                "projected_owner_id": goal["owner_id"],
                "home_shard_id": goal.get("home_shard_id", "K000"),
                "tenant_id": TENANT,
            }
        if "FROM benchmarks b" in compact and "JOIN goals g" in compact:
            benchmark = self.benchmarks.get(str(args[0]))
            if benchmark is None:
                return None
            goal = self.goals[str(benchmark["goal_id"])]
            return self._source_row(args[0]) if self._visible(sql, args, goal) else None
        if "FROM benchmarks b" in compact:
            benchmark = self.benchmarks.get(str(args[0]))
            if benchmark is None:
                return None
            return {
                "benchmark_id": benchmark["id"],
                "source_goal_id": benchmark["goal_id"],
                "benchmark_name": benchmark["name"],
                "benchmark_description": benchmark["description"],
                "benchmark_version": benchmark["version"],
                "benchmark_evaluation_protocol": benchmark["evaluation_protocol"],
                "benchmark_environment_specification": benchmark["environment_specification"],
                "benchmark_success_criteria": benchmark["success_criteria"],
                "benchmark_comparison_policy": benchmark["comparison_policy"],
                "benchmark_status": benchmark["status"],
                "benchmark_frozen_at": benchmark["frozen_at"],
                "benchmark_provenance": benchmark["provenance"],
                "benchmark_metadata": benchmark["metadata"],
            }
        if "FROM benchmarks" in compact and "WHERE id = $1::uuid" in compact:
            benchmark = self.benchmarks.get(str(args[0]))
            if benchmark is None:
                return None
            return {
                "benchmark_id": benchmark["id"],
                "source_goal_id": benchmark["goal_id"],
                "benchmark_name": benchmark["name"],
                "benchmark_description": benchmark["description"],
                "benchmark_version": benchmark["version"],
                "benchmark_evaluation_protocol": benchmark["evaluation_protocol"],
                "benchmark_environment_specification": benchmark["environment_specification"],
                "benchmark_success_criteria": benchmark["success_criteria"],
                "benchmark_comparison_policy": benchmark["comparison_policy"],
                "benchmark_status": benchmark["status"],
                "benchmark_frozen_at": benchmark["frozen_at"],
                "benchmark_provenance": benchmark["provenance"],
                "benchmark_metadata": benchmark["metadata"],
            }
        if "FROM goals" in compact and "WHERE id = $1::uuid" in compact:
            goal = self.goals.get(str(args[0]))
            return self._canonical_goal_row(args[0]) if goal is not None else None
        if "FROM goals g" in compact and "WHERE g.id = $1" in compact:
            goal = self.goals.get(str(args[0]))
            return self._target_row(args[0]) if goal is not None and self._visible(sql, args, goal) else None
        if "FROM goal_relations r" in compact and "LIMIT 1" in compact:
            return self._relation(args[0], args[1])
        if "FROM goal_relations r" in compact:
            source = str(args[0])
            rows = []
            for relation in self.relations:
                if relation["status"] != "accepted":
                    continue
                if source not in (relation["specific_goal_id"], relation["abstract_goal_id"]):
                    continue
                target = relation["abstract_goal_id"] if source == relation["specific_goal_id"] else relation["specific_goal_id"]
                goal = self.goals[target]
                if not self._visible(sql, args, goal):
                    continue
                row = self._target_row(target)
                row.update({k: relation[k] for k in ("specific_goal_id", "abstract_goal_id", "relation_type", "status", "confidence", "provenance", "decision_id", "decision_metadata", "decided_by", "decided_at", "updated_at", "scope_type", "scope_entity_id", "tenant_id")})
                rows.append(row)
            return rows
        if "FROM goal_relations" in compact:
            return self._relation(args[0], args[1])
        if "FROM benchmark_transfer_decisions" in compact and "WHERE transfer_key = $1" in compact:
            return self.lineage.get(str(args[0]))
        if compact.startswith("SELECT * FROM benchmarks WHERE goal_id"):
            for row in self.benchmarks.values():
                if str(row["goal_id"]) == str(args[0]) and row["name"] == args[1] and row["version"] == args[2]:
                    return dict(row)
            return None
        if compact.startswith("INSERT INTO benchmarks"):
            row = {
                "id": args[0],
                "goal_id": args[1],
                "name": args[2],
                "description": args[3],
                "version": 1,
                "evaluation_protocol": _decoded_jsonb(args[4]),
                "environment_specification": _decoded_jsonb(args[5]),
                "success_criteria": _decoded_jsonb(args[6]),
                "comparison_policy": _decoded_jsonb(args[7]),
                "status": "draft",
                "frozen_at": None,
                "provenance": args[8],
                "metadata": _decoded_jsonb(args[9]),
            }
            self.benchmarks[str(row["id"])] = row
            self.writes.append(("benchmark", row))
            return dict(row)
        if "WHERE benchmark_transfer_key = $1" in compact:
            for row in self.submissions.values():
                if row["benchmark_transfer_key"] == args[0]:
                    return dict(row)
            return None
        if compact.startswith("INSERT INTO benchmark_submissions"):
            row = {
                "id": args[0],
                "goal_id": args[1],
                "benchmark_id": args[2],
                "name": args[3],
                "description": args[4],
                "success_criteria": _decoded_jsonb(args[5]),
                "invariants": _decoded_jsonb(args[6]),
                "verification_method": _decoded_jsonb(args[7]),
                "status": "needs_review",
                "status_reason": args[8],
                "layer1_result": _decoded_jsonb(args[9]),
                "layer2_result": _decoded_jsonb(args[10]),
                "submitted_by": args[11],
                "provenance": args[12],
                "scope_type": args[13],
                "scope_entity_id": args[14],
                "owner_id": args[15],
                "visibility": args[16],
                "benchmark_transfer_key": args[17],
            }
            self.submissions[str(row["id"])] = row
            self.writes.append(("submission", row))
            return dict(row)
        if compact.startswith("INSERT INTO benchmark_transfer_decisions"):
            row = {
                "id": args[0],
                "transfer_key": args[1],
                "source_benchmark_id": args[2],
                "source_goal_id": args[3],
                "target_goal_id": args[4],
                "direction": args[5],
                "transfer_direction": args[6],
                "decision": args[7],
                "confidence": args[8],
                "reason": args[9],
                "provenance": _decoded_jsonb(args[10]),
                "source_fingerprint": args[11],
                "target_fingerprint": args[12],
                "relation_fingerprint": args[13],
                "policy_version": args[14],
                "judge_chain": args[15],
                "judge_provider": args[16],
                "judge_model": args[17],
                "prompt_version": args[18],
                "job_id": args[19],
                "target_benchmark_id": args[20],
                "benchmark_submission_id": args[21],
                "scope_type": args[22],
                "scope_entity_id": args[23],
                "owner_id": args[24],
                "visibility": args[25],
                "tenant_id": args[26],
            }
            if row["transfer_key"] in self.lineage:
                return None
            self.lineage[row["transfer_key"]] = row
            self.writes.append(("lineage", row))
            return dict(row)
        if "FROM goal_relations r" in compact and "LIMIT 1" in compact:
            return self._relation(args[0], args[1])
        if "FROM goal_relations r" in compact:
            source = str(args[0])
            rows = []
            for relation in self.relations:
                if relation["status"] != "accepted":
                    continue
                if source not in (relation["specific_goal_id"], relation["abstract_goal_id"]):
                    continue
                target = relation["abstract_goal_id"] if source == relation["specific_goal_id"] else relation["specific_goal_id"]
                goal = self.goals[target]
                if not self._visible(sql, args, goal):
                    continue
                row = self._target_row(target)
                row.update({k: relation[k] for k in ("specific_goal_id", "abstract_goal_id", "relation_type", "status", "confidence", "provenance", "decision_id", "decision_metadata", "decided_by", "decided_at", "updated_at", "scope_type", "scope_entity_id", "tenant_id")})
                rows.append(row)
            return rows
        raise AssertionError(f"unexpected fetchrow: {compact[:180]}")

    async def fetchval(self, sql, *args):
        compact = " ".join(sql.split())
        if compact.startswith("SELECT id FROM ingestion_jobs WHERE job_type"):
            for job in self.jobs:
                if job["job_type"] == args[0] and job["idempotency_key"] == args[1]:
                    return job["id"]
        return None

    async def fetch(self, sql, *args):
        compact = " ".join(sql.split())
        if "FROM goal_relations r" in compact:
            return await self.fetchrow(sql, *args)
        raise AssertionError(f"unexpected fetch: {compact[:180]}")


@asynccontextmanager
async def _transaction(pool, _tenant_scope):
    pool.transaction_count += 1
    yield pool


class Judge:
    chain_id = "test-chain"

    def __init__(self, relation="applies", confidence=0.93, reason="the contract applies", available=True):
        self.relation = relation
        self.confidence = confidence
        self.reason = reason
        self.available = available
        self.calls = []

    async def judge_identity_batch(self, kind, target, candidates):
        self.calls.append((kind, target, candidates))
        if not self.available:
            return SimpleNamespace(
                ok=False,
                value=None,
                provider=None,
                model=None,
                fallback_used=False,
                attempts=[],
                reason="all semantic providers failed",
            )
        return SimpleNamespace(
            ok=True,
            value=[{"relation": self.relation, "confidence": self.confidence, "reason": self.reason}],
            provider="test-provider",
            model="test-model",
            fallback_used=False,
            attempts=[],
        )


def _payload(pool, job):
    return {
        **job["payload"],
        "_job": {
            "id": job["id"],
            "attempt": 1,
            "idempotency_key": job["idempotency_key"],
            "scope_type": job["scope_type"],
            "scope_entity_id": job["scope_entity_id"],
            "owner_id": job["owner_id"],
            "visibility": job["visibility"],
        },
    }


@pytest.fixture
def pool(monkeypatch):
    value = FakePool()
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    return value


def _public_pool():
    value = FakePool()
    value.add_goal(_goal(G_SPECIFIC, "Specific goal"))
    value.add_goal(_goal(G_ABSTRACT, "Abstract goal"))
    value.add_goal(_goal(G_REJECTED, "Rejected neighbor"))
    value.add_benchmark(_benchmark(B_SPECIFIC, G_SPECIFIC, "Specific benchmark", status="frozen", frozen_at="2026-01-01"))
    value.add_benchmark(_benchmark(B_ABSTRACT, G_ABSTRACT, "Abstract benchmark"))
    value.add_relation(G_SPECIFIC, G_ABSTRACT)
    value.add_relation(G_ABSTRACT, G_REJECTED, status="rejected")
    return value


def test_handler_is_registered_for_the_leased_worker():
    from app.services.ingestion_jobs import JOB_HANDLERS

    assert JOB_HANDLERS[service.JOB_TYPE] is service.handle_benchmark_transfer


def test_idempotency_binds_relation_decision_identity_and_policy_version():
    base = service.benchmark_transfer_idempotency_key(
        B_SPECIFIC,
        G_ABSTRACT,
        source_fingerprint="source-a",
        target_fingerprint="target-a",
        relation_fingerprint="relation-a",
        policy_version=service.TRANSFER_VERSION,
    )

    assert base != service.benchmark_transfer_idempotency_key(
        B_SPECIFIC,
        G_ABSTRACT,
        source_fingerprint="source-a",
        target_fingerprint="target-a",
        relation_fingerprint="relation-b",
        policy_version=service.TRANSFER_VERSION,
    )
    assert base != service.benchmark_transfer_idempotency_key(
        B_SPECIFIC,
        G_ABSTRACT,
        source_fingerprint="source-a",
        target_fingerprint="target-a",
        relation_fingerprint="relation-a",
        policy_version="benchmark_transfer@v2",
    )


@pytest.mark.asyncio
async def test_both_relation_directions_are_enqueued_and_rejected_edges_are_excluded(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    first = await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    duplicate = await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    reverse = await service.enqueue_benchmark_transfer(value, B_ABSTRACT, G_SPECIFIC)

    assert first[0] == duplicate[0]
    assert first[1] is True and duplicate[1] is False and reverse[1] is True
    assert {job["payload"]["direction"] for job in value.jobs} == {
        service.SPECIFIC_TO_ABSTRACT,
        service.ABSTRACT_TO_SPECIFIC,
    }
    with pytest.raises(service.BenchmarkTransferError):
        await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_REJECTED)
    neighbors = await service.enqueue_benchmark_transfers(value, B_SPECIFIC)
    assert [item["target_goal_id"] for item in neighbors] == [G_ABSTRACT]
    key = service.benchmark_transfer_idempotency_key(B_SPECIFIC, G_ABSTRACT, direction=service.SPECIFIC_TO_ABSTRACT)
    assert key == service.benchmark_transfer_idempotency_key(
        B_SPECIFIC, G_ABSTRACT, direction=service.SPECIFIC_TO_ABSTRACT
    )


@pytest.mark.asyncio
async def test_transfer_creates_draft_and_needs_review_without_source_state_propagation(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    job = value.jobs[0]
    judge = Judge()
    result = await service.handle_benchmark_transfer(value, _payload(value, job), judge=judge)

    source_before = dict(value.benchmarks[B_SPECIFIC])
    target = next(
        row for row in value.benchmarks.values()
        if row["goal_id"] == G_ABSTRACT and row["id"] != B_ABSTRACT
    )
    submission = next(iter(value.submissions.values()))
    lineage = next(iter(value.lineage.values()))

    assert result["decision"] == "transferable"
    assert judge.calls[0][0] == "benchmark_transfer"
    assert source_before["status"] == "frozen"
    assert target["status"] == "draft" and target["frozen_at"] is None
    assert "Abstract goal" in target["name"]
    assert submission["benchmark_transfer_key"] == lineage["transfer_key"]
    assert submission["status"] == "needs_review"
    assert submission["verification_method"] == {}
    assert submission["layer1_result"] == {} and submission["layer2_result"] == {}
    assert "secret_evidence" not in json.dumps(target)
    assert "verification_state" not in json.dumps(target)
    assert lineage["source_benchmark_id"] == B_SPECIFIC
    assert lineage["target_goal_id"] == G_ABSTRACT
    assert lineage["direction"] == service.SPECIFIC_TO_ABSTRACT
    assert lineage["confidence"] == 0.93
    assert lineage["judge_provider"] == "test-provider"
    assert lineage["judge_model"] == "test-model"
    assert lineage["provenance"]["judge"]["operation"] == "judge_identity_batch"
    assert all("benchmark_cases" not in kind for kind, _row in value.writes)
    assert all("evaluations" not in kind for kind, _row in value.writes)
    assert all("evidence" not in kind for kind, _row in value.writes)


@pytest.mark.asyncio
async def test_remote_source_and_target_goals_are_hydrated_with_central_benchmark_writes(pool, monkeypatch):
    control = _public_pool()
    remote = FakePool()
    for goal_id in (G_SPECIFIC, G_ABSTRACT):
        control.goals[goal_id]["home_shard_id"] = "K001"
        remote.add_goal(dict(control.goals[goal_id]))

    async def routed(_pool, _object_type, _object_id):
        return remote

    monkeypatch.setattr(service, "home_pool", routed)
    await service.enqueue_benchmark_transfer(control, B_SPECIFIC, G_ABSTRACT)
    payload = _payload(control, control.jobs[0])

    result = await service.handle_benchmark_transfer(control, payload, judge=Judge())

    assert result["decision"] == "transferable"
    assert len(control.submissions) == 1
    assert not remote.submissions
    assert control.benchmarks[B_SPECIFIC]["status"] == "frozen"


@pytest.mark.asyncio
async def test_post_validation_failure_compensates_new_transfer_artifacts(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    payload = _payload(value, value.jobs[0])
    original = service._validate_transfer_state
    calls = 0

    async def fail_after_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise service.BenchmarkTransferStale("post validation race")
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "_validate_transfer_state", fail_after_write)
    with pytest.raises(service.BenchmarkTransferStale):
        await service.handle_benchmark_transfer(value, payload, judge=Judge())

    assert len(value.benchmarks) == 2
    assert not value.submissions
    assert not value.lineage
    assert [kind for kind, _row in value.writes].count("delete_lineage") == 1
    assert [kind for kind, _row in value.writes].count("delete_submission") == 1
    assert [kind for kind, _row in value.writes].count("delete_benchmark") == 1


@pytest.mark.asyncio
async def test_semantic_decisions_materialize_only_transferable_and_partial(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    job = value.jobs[0]
    await service.handle_benchmark_transfer(value, _payload(value, job), judge=Judge(relation="partial"))
    assert len(value.benchmarks) == 3 and len(value.submissions) == 1

    value.add_goal(_goal("00000000-0000-4000-8000-000000000104", "Uncertain goal"))
    value.add_benchmark(_benchmark("00000000-0000-4000-8000-000000000203", "00000000-0000-4000-8000-000000000104", "Uncertain benchmark"))
    value.add_relation("00000000-0000-4000-8000-000000000104", G_ABSTRACT)
    await service.enqueue_benchmark_transfer(value, "00000000-0000-4000-8000-000000000203", G_ABSTRACT)
    uncertain_job = value.jobs[-1]
    uncertain = await service.handle_benchmark_transfer(
        value, _payload(value, uncertain_job), judge=Judge(relation="uncertain", confidence=0.2)
    )
    assert uncertain["decision"] == "uncertain"
    assert len(value.benchmarks) == 4 and len(value.submissions) == 1
    assert len(value.lineage) == 2


@pytest.mark.asyncio
async def test_frozen_source_race_fails_closed_before_artifacts(pool, monkeypatch):
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    payload = _payload(value, value.jobs[0])
    original = value.benchmarks[B_SPECIFIC]

    async def race(*_args, **_kwargs):
        value.benchmarks[B_SPECIFIC] = {**original, "status": "active", "frozen_at": None}
        return service.TransferDecision("transferable", 0.93, "race", {})

    monkeypatch.setattr(service, "_judge_transfer", race)
    with pytest.raises(service.BenchmarkTransferStale):
        await service.handle_benchmark_transfer(value, payload, judge=Judge())

    assert len(value.benchmarks) == 2 and not value.submissions and not value.lineage


@pytest.mark.asyncio
async def test_relation_decision_race_fails_closed_before_artifacts(pool, monkeypatch):
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    payload = _payload(value, value.jobs[0])
    relation = next(row for row in value.relations if row["status"] == "accepted")

    async def race(*_args, **_kwargs):
        relation["decision_id"] = "00000000-0000-4000-8000-000000000399"
        return service.TransferDecision("transferable", 0.93, "race", {})

    monkeypatch.setattr(service, "_judge_transfer", race)
    with pytest.raises(service.BenchmarkTransferStale):
        await service.handle_benchmark_transfer(value, payload, judge=Judge())

    assert len(value.benchmarks) == 2 and not value.submissions and not value.lineage


@pytest.mark.asyncio
async def test_retry_is_idempotent_and_judge_runs_once(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    job = value.jobs[0]
    judge = Judge()
    first = await service.handle_benchmark_transfer(value, _payload(value, job), judge=judge)
    second = await service.handle_benchmark_transfer(value, _payload(value, job), judge=judge)

    assert first["replayed"] is False and second["replayed"] is True
    assert len(judge.calls) == 1
    assert len(value.benchmarks) == 3 and len(value.submissions) == 1 and len(value.lineage) == 1


@pytest.mark.asyncio
async def test_judge_outage_writes_nothing_then_retry_converges(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    job = value.jobs[0]
    with pytest.raises(SemanticJudgmentUnavailable):
        await service.handle_benchmark_transfer(
            value, _payload(value, job), judge=Judge(available=False)
        )
    assert len(value.benchmarks) == 2 and not value.submissions and not value.lineage

    result = await service.handle_benchmark_transfer(value, _payload(value, job), judge=Judge())
    assert result["decision"] == "transferable"
    assert len(value.benchmarks) == 3 and len(value.submissions) == 1 and len(value.lineage) == 1


@pytest.mark.asyncio
async def test_same_scope_privacy_is_enforced_before_enqueue_and_write(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = FakePool()
    value.add_goal(_goal(G_SPECIFIC, "Alice goal", owner_id="alice", visibility="private"))
    value.add_goal(_goal(G_ABSTRACT, "Bob goal", owner_id="bob", visibility="private"))
    value.add_benchmark(_benchmark(B_SPECIFIC, G_SPECIFIC, "Alice benchmark"))
    value.add_relation(G_SPECIFIC, G_ABSTRACT)

    with pytest.raises(service.BenchmarkTransferError):
        await service.enqueue_benchmark_transfer(
            value, B_SPECIFIC, G_ABSTRACT, access_scope=AccessScope.for_user("alice")
        )
    assert not value.jobs and not value.submissions and not value.lineage

    value.add_goal(_goal("00000000-0000-4000-8000-000000000105", "Public goal"))
    value.add_benchmark(_benchmark(B_ABSTRACT, "00000000-0000-4000-8000-000000000105", "Public benchmark"))
    value.add_relation("00000000-0000-4000-8000-000000000105", G_ABSTRACT)
    with pytest.raises(service.BenchmarkTransferPrivacyError):
        await service.enqueue_benchmark_transfer(
            value, B_ABSTRACT, G_ABSTRACT, access_scope=AccessScope.unrestricted()
        )
    assert not value.submissions and not value.lineage


@pytest.mark.asyncio
async def test_forged_job_scope_cannot_transfer_private_content(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = FakePool()
    value.add_goal(_goal(G_SPECIFIC, "Alice goal", owner_id="alice", visibility="private"))
    value.add_goal(_goal(G_ABSTRACT, "Alice abstract", owner_id="alice", visibility="private"))
    value.add_benchmark(_benchmark(B_SPECIFIC, G_SPECIFIC, "Alice benchmark"))
    value.add_relation(G_SPECIFIC, G_ABSTRACT)
    await service.enqueue_benchmark_transfer(
        value, B_SPECIFIC, G_ABSTRACT, access_scope=AccessScope.for_user("alice")
    )
    job = value.jobs[0]
    payload = _payload(value, job)
    payload["_job"]["owner_id"] = "mallory"
    with pytest.raises(service.BenchmarkTransferPrivacyError):
        await service.handle_benchmark_transfer(
            value, payload, judge=Judge(), access_scope=AccessScope.for_user("alice")
        )
    assert not value.submissions and not value.lineage


@pytest.mark.asyncio
async def test_not_transferable_records_lineage_without_target_artifacts(pool, monkeypatch):
    monkeypatch.setattr(service, "tenant_transaction", _transaction)
    value = _public_pool()
    await service.enqueue_benchmark_transfer(value, B_SPECIFIC, G_ABSTRACT)
    job = value.jobs[0]
    result = await service.handle_benchmark_transfer(
        value, _payload(value, job), judge=Judge(relation="not_applicable")
    )

    assert result["decision"] == "not_transferable"
    assert len(value.benchmarks) == 2
    assert not value.submissions
    assert len(value.lineage) == 1
    assert next(iter(value.lineage.values()))["target_benchmark_id"] is None


def test_source_structure_validation_rejects_incomplete_benchmark(pool):
    row = _public_pool()._source_row(B_SPECIFIC)
    row["benchmark_name"] = " "
    with pytest.raises(service.BenchmarkSourceInvalid):
        service.validate_source_structure(row)


def test_source_structure_requires_frozen_status_and_timestamp(pool):
    row = _public_pool()._source_row(B_SPECIFIC)
    row["benchmark_status"] = "active"
    row["benchmark_frozen_at"] = None
    with pytest.raises(service.BenchmarkSourceInvalid):
        service.validate_source_structure(row)


def test_migration_is_additive_idempotent_and_has_no_backfill():
    sql = (Path(__file__).parents[1] / "db" / "114_benchmark_transfer.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS benchmark_transfer_decisions" in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmark_transfer_decisions_key" in sql
    assert "benchmark_transfer_key" in sql
    assert "tg_ref_benchmark_submissions_goal_id" in sql
    assert "DROP CONSTRAINT benchmark_submissions_goal_id_fkey" in sql
    assert "relation_fingerprint" in sql
    assert "policy_version" in sql
    assert "benchmark_transfer_visibility_v1_chk" in sql
    assert "sl_benchmark_transfer_ref_exists" in sql
    assert "DROP TRIGGER IF EXISTS" in sql
    assert "INSERT INTO" not in sql
    assert "DROP TABLE" not in sql
    assert "benchmark_transfer_decisions_append_only" in sql
    assert "app.benchmark_transfer_compensation" in sql
