from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.ingestion.worker import is_retryable
from app.services import ingestion_jobs, identity_resolution
from app.services.goal_abstraction import GoalRelationScopeError
from app.services.goals import find_or_create_goal
from app.services.identity_resolution import Candidate
from app.services.semantic.errors import SemanticJudgmentUnavailable

NEW_GOAL = "00000000-0000-4000-8000-000000000001"
EXISTING_GOAL = "00000000-0000-4000-8000-000000000002"
DECISION = "00000000-0000-4000-8000-000000000003"


class GoalPool:
    def __init__(self, *, fail_enqueue: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_enqueue = fail_enqueue
        self.next_id = 10

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        compact = " ".join(sql.split())
        if compact.startswith("SELECT id, canonical_name FROM goals"):
            normalized = str(args[0])
            candidate_name = str(args[1])
            for row in self.rows:
                aliases = {str(value).strip().lower() for value in row.get("aliases") or []}
                if row.get("normalized_name") == normalized or candidate_name.strip().lower() in aliases:
                    return row
            return None
        if compact.startswith("INSERT INTO goals"):
            row = {
                "id": str(args[0]),
                "canonical_name": args[1],
                "normalized_name": args[2],
                "aliases": args[14],
                "status": args[9],
                "scope_type": args[16],
                "scope_entity_id": args[17],
                "version": 1,
                "home_shard_id": args[-1],
            }
            self.rows.append(row)
            return row
        raise AssertionError(f"unexpected fetchrow: {compact}")

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        compact = " ".join(sql.split())
        if "FROM knowledge_shards" in compact:
            return [
                {
                    "shard_id": "K000",
                    "status": "active",
                    "weight": 100,
                    "dsn_env": None,
                    "capacity_rows": None,
                }
            ]
        if "FROM goals" in compact:
            return []
        raise AssertionError(f"unexpected fetch: {compact}")

    async def execute(self, sql: str, *args: Any) -> str:
        compact = " ".join(sql.split())
        if "INSERT INTO ingestion_jobs" in compact:
            if self.fail_enqueue:
                raise RuntimeError("queue unavailable")
            key = (str(args[0]), str(args[2]))
            if key in self.jobs:
                return "INSERT 0 0"
            self.jobs[key] = {
                "job_type": args[0],
                "payload": args[1],
                "idempotency_key": args[2],
                "scope_type": args[4],
                "scope_entity_id": args[5],
                "owner_id": args[6],
                "visibility": args[7],
            }
            self.next_id += 1
            return "INSERT 0 1"
        return "UPDATE 0"


def anchor() -> dict[str, Any]:
    return {
        "id": NEW_GOAL,
        "canonical_name": "Deploy the service safely",
        "description": "Deploy without regressions",
        "status": "active",
        "scope_type": "global",
        "scope_entity_id": None,
        "visibility": "public",
        "owner_id": None,
        "version": 1,
        "home_shard_id": "K000",
    }


def existing_goal() -> dict[str, Any]:
    return {
        "id": EXISTING_GOAL,
        "canonical_name": "Deploy services safely",
        "description": "General safe deployment",
        "status": "active",
        "scope_type": "global",
        "scope_entity_id": None,
        "visibility": "public",
        "owner_id": None,
        "version": 1,
        "home_shard_id": "K000",
    }


def payload(*, decision_id: str | None = DECISION) -> dict[str, Any]:
    key = identity_resolution.goal_abstraction_placement_key(NEW_GOAL)
    return {
        "goal_id": NEW_GOAL,
        "identity_decision_id": decision_id,
        "goal_version": 1,
        "candidate_limit": 20,
        "neighbor_seed_limit": 5,
        "neighbor_limit": 20,
        "minimum_confidence": 0.9,
        "idempotency_key": key,
        "_job": {
            "id": 41,
            "attempt": 1,
            "idempotency_key": key,
            "scope_type": "global",
            "scope_entity_id": None,
            "owner_id": None,
            "visibility": "public",
        },
    }


def remembered_decision() -> dict[str, Any]:
    return {
        "id": DECISION,
        "candidate_text": "Deploy the service safely: Deploy without regressions",
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "narrower",
        "candidates": [
            Candidate(
                id=EXISTING_GOAL,
                name="Deploy services safely",
                text="Deploy services safely: General safe deployment",
                relation="specializes",
                confidence=0.96,
            )
        ],
        "judge_provider": "test",
        "judge_model": "test-model",
    }


def wire_handler(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidates: list[Candidate],
    relation_states: dict[tuple[str, str], str] | None = None,
    decision: dict[str, Any] | None = remembered_decision(),
    judge: Any = None,
    adjudicator: Any = None,
    persister: Any = None,
    audits: list[str] | None = None,
) -> dict[str, Any]:
    states = relation_states if relation_states is not None else {}
    audit_reasons = audits if audits is not None else []

    async def expand(_pool: Any, goal_id: str, **_kwargs: Any) -> dict[str, Any]:
        goal = anchor() if goal_id == NEW_GOAL else None
        return {
            "goal": goal,
            "parents": [],
            "children": [],
            "partial": False,
            "unavailable_shards": {},
            "missing_ids": [],
        }

    async def load_embedding(_pool: Any, _goal_id: str) -> tuple[None, None]:
        return None, None

    async def generate(*_args: Any, **_kwargs: Any) -> tuple[list[Candidate], int, int]:
        return list(candidates), len(candidates), 0

    async def load_decision(_pool: Any, decision_id: str) -> dict[str, Any] | None:
        return decision if decision and decision["id"] == decision_id else None

    async def relation_state(_pool: Any, specific: str, abstract: str, _tenant: Any) -> str | None:
        return states.get((specific, abstract))

    async def default_adjudicate(_pool: Any, specific: str, abstract: str, **_kwargs: Any) -> dict[str, Any]:
        by_id = {NEW_GOAL: anchor(), EXISTING_GOAL: existing_goal()}
        return {
            "specific_goal": by_id[specific],
            "abstract_goal": by_id[abstract],
        }

    async def default_persist(
        _pool: Any,
        specific: str,
        abstract: str,
        **values: Any,
    ) -> dict[str, Any]:
        states[(specific, abstract)] = str(values["status"])
        return {"created": True, "persisted": True}

    async def enqueue_audit(
        _pool: Any,
        goal_id: str,
        *,
        reason: str,
        **_kwargs: Any,
    ) -> tuple[None, bool]:
        audit_reasons.append(reason)
        return None, True

    monkeypatch.setattr(ingestion_jobs, "pools_for", lambda _pool: object())
    monkeypatch.setattr(ingestion_jobs, "expand_goal_neighbors", expand)
    monkeypatch.setattr(ingestion_jobs, "_load_goal_embedding", load_embedding)
    monkeypatch.setattr(ingestion_jobs, "generate_goal_candidates", generate)
    monkeypatch.setattr(ingestion_jobs, "load_goal_identity_decision", load_decision)
    monkeypatch.setattr(ingestion_jobs, "_goal_relation_state", relation_state)
    monkeypatch.setattr(
        ingestion_jobs,
        "adjudicate_goal_relation",
        adjudicator or default_adjudicate,
    )
    monkeypatch.setattr(
        ingestion_jobs,
        "persist_goal_relation",
        persister or default_persist,
    )
    monkeypatch.setattr(ingestion_jobs, "enqueue_goal_abstraction_audit", enqueue_audit)
    redundant_edges: set[tuple[str, str]] = set()
    transfers: list[tuple[str, str]] = []

    async def redundant(_pool: Any, specific: str, abstract: str) -> bool:
        return (specific, abstract) in redundant_edges

    async def neighbor_transfers(_pool: Any, goal_id: str, edges: Any, **_kwargs: Any) -> int:
        transfers.extend((goal_id, edge[1] if edge[0] == goal_id else edge[0]) for edge in edges)
        return len(edges)

    monkeypatch.setattr(ingestion_jobs, "is_accepted_edge_redundant", redundant)
    monkeypatch.setattr(ingestion_jobs, "_enqueue_neighbor_benchmark_transfers", neighbor_transfers)
    return {"states": states, "audits": audit_reasons, "redundant": redundant_edges, "transfers": transfers}


@pytest.mark.asyncio
async def test_new_goal_enqueues_one_idempotent_placement_job():
    pool = GoalPool()

    created = await find_or_create_goal(
        pool,
        canonical_name="Deploy the service safely",
        scope_type="global",
        provenance="system_pending_review",
    )

    assert created["created"] is True
    assert [job["job_type"] for job in pool.jobs.values()] == [
        identity_resolution.GOAL_ABSTRACTION_PLACEMENT_JOB
    ]
    await asyncio.gather(
        *[
            identity_resolution.enqueue_goal_abstraction_placement(
                pool,
                created["id"],
                identity_decision_id=None,
                scope_type="global",
                scope_entity_id=None,
                owner_id=None,
                visibility="public",
            )
            for _ in range(8)
        ]
    )
    assert len(pool.jobs) == 1


def test_org_placement_uses_trusted_job_tenant():
    tenant_id = "00000000-0000-4000-8000-0000000000aa"
    body = payload(decision_id=None)
    body["_job"].update({
        "scope_type": "organization",
        "scope_entity_id": tenant_id,
        "owner_id": "alice",
        "visibility": "org",
    })

    context = ingestion_jobs._placement_context(body)

    assert context["tenant_scope"].tenant_id == tenant_id
    assert context["access_scope"].org_ids == (tenant_id,)


@pytest.mark.asyncio
async def test_reused_goal_does_not_enqueue_placement():
    pool = GoalPool()
    pool.rows.append(
        {
            "id": EXISTING_GOAL,
            "canonical_name": "Deploy the service safely",
            "normalized_name": "deploy the service safely",
            "aliases": [],
            "status": "active",
            "scope_type": "global",
            "scope_entity_id": None,
        }
    )

    result = await find_or_create_goal(
        pool,
        canonical_name="deploy   the service safely",
        scope_type="global",
        provenance="system_pending_review",
    )

    assert result["created"] is False
    assert not pool.jobs


@pytest.mark.asyncio
async def test_placement_enqueue_failure_does_not_fail_goal_creation():
    pool = GoalPool(fail_enqueue=True)

    result = await find_or_create_goal(
        pool,
        canonical_name="Deploy the service safely",
        scope_type="global",
        provenance="system_pending_review",
    )

    assert result["created"] is True
    assert len(pool.rows) == 1


@pytest.mark.asyncio
async def test_accepted_directional_edge_uses_goal_abstraction_authority(monkeypatch):
    writes: list[dict[str, Any]] = []
    candidate = Candidate(EXISTING_GOAL, existing_goal()["canonical_name"], "General safe deployment")
    state = wire_handler(monkeypatch, candidates=[candidate])

    async def persist(_pool: Any, specific: str, abstract: str, **values: Any) -> dict[str, Any]:
        writes.append({"specific": specific, "abstract": abstract, **values})
        state["states"][(specific, abstract)] = "accepted"
        return {"created": True, "persisted": True}

    monkeypatch.setattr(ingestion_jobs, "persist_goal_relation", persist)
    result = await ingestion_jobs.handle_goal_abstraction_placement(
        object(), payload(), judge=object()
    )

    assert result["accepted_edges"] == 1
    assert writes[0]["specific"] == NEW_GOAL
    assert writes[0]["abstract"] == EXISTING_GOAL
    assert writes[0]["status"] == "accepted"
    assert writes[0]["confidence"] == 0.96
    assert writes[0]["expected_status"] is None
    assert writes[0]["decision_metadata"]["policy"] == "goal_abstraction_auto_acceptance"
    assert writes[0]["decision_metadata"]["policy_version"] == "goal_abstraction_placement_v1"
    assert writes[0]["decision_metadata"]["authority"] == "goal_abstraction_ingestion_worker"


@pytest.mark.asyncio
async def test_jev_outage_is_retryable_and_writes_no_edge(monkeypatch):
    candidate = Candidate(EXISTING_GOAL, existing_goal()["canonical_name"], "General safe deployment")
    state = wire_handler(monkeypatch, candidates=[candidate], decision=None)

    class Judge:
        async def judge_identity_batch(self, *_args: Any) -> Any:
            return SimpleNamespace(
                ok=False,
                value=None,
                reason="all providers failed",
                attempts=[],
                provider=None,
                model=None,
            )

    with pytest.raises(SemanticJudgmentUnavailable):
        await ingestion_jobs.handle_goal_abstraction_placement(
            object(), payload(decision_id=None), judge=Judge()
        )

    assert is_retryable(SemanticJudgmentUnavailable("outage"))
    assert not state["states"]


@pytest.mark.asyncio
async def test_idempotent_replay_creates_no_duplicate_edge(monkeypatch):
    candidate = Candidate(EXISTING_GOAL, existing_goal()["canonical_name"], "General safe deployment")
    state = wire_handler(monkeypatch, candidates=[candidate])
    writes: list[tuple[str, str]] = []

    async def persist(_pool: Any, specific: str, abstract: str, **values: Any) -> dict[str, Any]:
        writes.append((specific, abstract))
        state["states"][(specific, abstract)] = "accepted"
        return {"created": True, "persisted": True}

    monkeypatch.setattr(ingestion_jobs, "persist_goal_relation", persist)
    first = await ingestion_jobs.handle_goal_abstraction_placement(object(), payload(), judge=object())
    second = await ingestion_jobs.handle_goal_abstraction_placement(object(), payload(), judge=object())

    assert first["accepted_edges"] == 1
    assert second["accepted_edges"] == 1
    assert writes == [(NEW_GOAL, EXISTING_GOAL)]


@pytest.mark.asyncio
async def test_orphan_goal_succeeds_and_enqueues_audit(monkeypatch):
    audits: list[str] = []
    wire_handler(monkeypatch, candidates=[], decision=None, audits=audits)

    class Judge:
        async def judge_identity_batch(self, *_args: Any) -> Any:
            raise AssertionError("orphan placement must not call a judge")

    result = await ingestion_jobs.handle_goal_abstraction_placement(
        object(), payload(decision_id=None), judge=Judge()
    )

    assert result["accepted_edges"] == 0
    assert result["candidates"] == 0
    assert audits == ["orphan"]


@pytest.mark.asyncio
async def test_scope_rejection_never_persists_an_edge(monkeypatch):
    candidate = Candidate(EXISTING_GOAL, existing_goal()["canonical_name"], "General safe deployment")
    state = wire_handler(monkeypatch, candidates=[candidate])

    async def reject(*_args: Any, **_kwargs: Any) -> None:
        raise GoalRelationScopeError("cross-scope")

    monkeypatch.setattr(ingestion_jobs, "adjudicate_goal_relation", reject)
    result = await ingestion_jobs.handle_goal_abstraction_placement(
        object(), payload(), judge=object()
    )

    assert result["accepted_edges"] == 0
    assert result["rejected_edges"] == 1
    assert not state["states"]


@pytest.mark.asyncio
async def test_placement_prunes_an_edge_its_own_later_edge_implies(monkeypatch):
    # The judge says the new Goal specializes both EXISTING and that Goal's
    # parent; once both are accepted, the edge to the ancestor is implied.
    parent_goal = "01900000-0000-7000-8000-00000000abcd"
    candidates = [
        Candidate(parent_goal, "Ship software", "Ship software"),
        Candidate(EXISTING_GOAL, existing_goal()["canonical_name"], "General safe deployment"),
    ]
    writes: list[dict[str, Any]] = []

    async def adjudicate(_pool: Any, specific: str, abstract: str, **_kwargs: Any) -> dict[str, Any]:
        public = {"visibility": "public", "owner_id": None}
        return {"specific_goal": public, "abstract_goal": public}

    async def persist(_pool: Any, specific: str, abstract: str, **values: Any) -> dict[str, Any]:
        writes.append({"specific": specific, "abstract": abstract, **values})
        state["states"][(specific, abstract)] = values["status"]
        return {"created": True, "persisted": True}

    class Judge:
        async def judge_identity_batch(self, kind: str, a: str, texts: list[str]) -> Any:
            class R:
                ok = True
                provider = "fake"
                model = "fake"
                value = [{"relation": "specializes", "confidence": 0.95} for _ in texts]
            return R()

    state = wire_handler(monkeypatch, candidates=candidates, decision=None, adjudicator=adjudicate, persister=persist)
    state["redundant"].add((NEW_GOAL, parent_goal))
    monkeypatch.setattr(ingestion_jobs, "record_decision", _record_nothing)
    result = await ingestion_jobs.handle_goal_abstraction_placement(
        object(), {**payload(), "identity_decision_id": None}, judge=Judge()
    )

    assert result["pruned_redundant_edges"] == 1
    assert result["accepted_edges"] == 1
    pruned = [w for w in writes if w["status"] == "rejected"]
    assert [(w["specific"], w["abstract"]) for w in pruned] == [(NEW_GOAL, parent_goal)]
    assert pruned[0]["expected_status"] == "accepted"
    assert pruned[0]["decision_metadata"]["outcome"] == "transitively_redundant"
    # Only the surviving direct neighbour's Benchmarks become transfer candidates.
    assert state["transfers"] == [(NEW_GOAL, EXISTING_GOAL)]


async def _record_nothing(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_neighbor_benchmark_enrichment_enqueues_frozen_benchmarks_and_never_fails(monkeypatch):
    from app.services import benchmark_transfer

    queries: list[tuple[str, tuple]] = []

    class Pool:
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            queries.append((" ".join(sql.split()), args))
            return [{"id": "b-ok"}, {"id": "b-broken"}]

    enqueued: list[tuple[str, str]] = []

    async def enqueue(_pool: Any, benchmark_id: str, target_goal_id: str, **_kwargs: Any) -> tuple[int, bool]:
        if benchmark_id == "b-broken":
            raise RuntimeError("source no longer visible")
        enqueued.append((benchmark_id, target_goal_id))
        return 1, True

    monkeypatch.setattr(benchmark_transfer, "enqueue_benchmark_transfer", enqueue)
    context = {"access_scope": object(), "tenant_scope": object()}
    edges = [(NEW_GOAL, EXISTING_GOAL, None, {})]
    count = await ingestion_jobs._enqueue_neighbor_benchmark_transfers(Pool(), NEW_GOAL, edges, context=context)

    assert count == 1 and enqueued == [("b-ok", NEW_GOAL)]
    sql, args = queries[0]
    assert "status = 'frozen'" in sql and args[0] == [EXISTING_GOAL] and args[1] == 20


@pytest.mark.asyncio
async def test_neighbor_benchmark_enrichment_without_new_edges_does_nothing():
    class Pool:
        async def fetch(self, *_args: Any) -> list[dict[str, Any]]:
            raise AssertionError("no lookup without a newly connected neighbour")

    assert await ingestion_jobs._enqueue_neighbor_benchmark_transfers(
        Pool(), NEW_GOAL, [], context={"access_scope": None, "tenant_scope": None}) == 0


@pytest.mark.asyncio
async def test_placement_repair_sweep_reenqueues_goals_without_a_placement_job(monkeypatch):
    import app.services.identity_resolution as ir

    class Pool:
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            assert "NOT EXISTS (SELECT 1 FROM ingestion_jobs" in " ".join(sql.split())
            return [
                {"id": NEW_GOAL, "scope_type": "global", "scope_entity_id": None, "owner_id": None,
                 "visibility": "public", "version": 1},
                {"id": EXISTING_GOAL, "scope_type": "global", "scope_entity_id": None, "owner_id": None,
                 "visibility": "public", "version": 2},
            ]

    calls: list[str] = []

    async def enqueue(_pool: Any, goal_id: str, **kwargs: Any) -> tuple[int, bool]:
        if goal_id == EXISTING_GOAL:
            raise RuntimeError("queue briefly unavailable")
        calls.append(goal_id)
        assert kwargs["identity_decision_id"] is None and kwargs["scope_entity_id"] is None
        return 7, True

    monkeypatch.setattr(ir, "enqueue_goal_abstraction_placement", enqueue)
    assert await ir.enqueue_missing_goal_placements(Pool()) == 1
    assert calls == [NEW_GOAL]
