from __future__ import annotations

import pytest

from app.services import identity_resolution, procedure_identity
from app.services.identity_resolution import (
    Candidate,
    IdentityReplayError,
    PermanentIdentityConflict,
    identity_idempotency_key,
    resolve_goal_identity,
)
from app.services.procedure_identity import procedure_text, resolve_procedure_identity
from app.services.semantic.chain import ChainResult


class ReplayPool:
    def __init__(self):
        self.prior = None
        self.inserts = []

    async def fetchrow(self, sql, *params):
        normalized = " ".join(sql.split())
        if "FROM identity_decisions" in normalized:
            return self.prior
        if "INSERT INTO identity_decisions" in normalized:
            self.inserts.append(params)
            self.prior = {
                "id": "decision-1",
                "candidate_text": params[1],
                "scope_type": params[2],
                "scope_entity_id": params[3],
                "decision": params[4],
                "resolved_id": params[5],
                "candidates": params[6],
                "judge_provider": params[8],
                "judge_model": params[9],
                "fts_candidates": params[11],
                "vector_candidates": params[12],
                "job_id": params[13],
                "idempotency_key": params[14],
                "detail": params[15],
            }
            return {"id": "decision-1"}
        if "FROM goals" in normalized:
            return {"scope_type": "global", "scope_entity_id": None}
        if "FROM procedures" in normalized and "achieves_goal_id" in normalized:
            return {
                "achieves_goal_id": "goal-1",
                "scope_type": "global",
                "scope_entity_id": None,
            }
        return None

    async def fetchval(self, sql, *params):
        normalized = " ".join(sql.split())
        if "FROM goals" in normalized or "FROM procedures" in normalized:
            return 1
        return None


class CountingJudge:
    providers = ("fake",)
    chain_id = "fake:test"

    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    async def judge_identity_batch(self, kind, text, candidates):
        self.batch_calls += 1
        return ChainResult(
            ok=True,
            value=[{"relation": "distinct", "confidence": 0.99} for _ in candidates],
            provider="fake",
            model="test",
        )

    async def judge_identity(self, kind, text, candidate):
        self.single_calls += 1
        return ChainResult(
            ok=True,
            value={"relation": "distinct", "confidence": 0.99},
            provider="fake",
            model="test",
        )


@pytest.mark.asyncio
async def test_identity_key_is_deterministic_and_rejects_invalid_job_ids():
    first = identity_idempotency_key(
        job_id=17,
        source_hash="a" * 64,
        object_type="goal",
        semantic_role="step_goal",
        scope_type="global",
        scope_entity_id=None,
        text="Find   Callers",
    )
    second = identity_idempotency_key(
        job_id=17,
        source_hash="a" * 64,
        object_type="goal",
        semantic_role="step_goal",
        scope_type="global",
        scope_entity_id=None,
        text="find callers",
    )
    assert first == second
    assert len(first) == 64
    assert identity_idempotency_key(
        job_id=None,
        source_hash="a" * 64,
        object_type="goal",
        semantic_role="step_goal",
        scope_type="global",
        scope_entity_id=None,
        text="find callers",
    ) is None
    with pytest.raises(ValueError):
        identity_idempotency_key(
            job_id=True,
            source_hash="a" * 64,
            object_type="goal",
            semantic_role="step_goal",
            scope_type="global",
            scope_entity_id=None,
            text="find callers",
        )
    with pytest.raises(ValueError):
        identity_idempotency_key(
            job_id=0,
            source_hash="a" * 64,
            object_type="goal",
            semantic_role="step_goal",
            scope_type="global",
            scope_entity_id=None,
            text="find callers",
        )


@pytest.mark.asyncio
async def test_goal_create_decision_replays_without_candidates_or_judging(monkeypatch):
    pool = ReplayPool()
    judge = CountingJudge()
    candidate_calls = 0

    async def candidates(*_args, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return [Candidate("candidate-1", "existing", "existing goal")], 1, 0

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    key = identity_idempotency_key(
        job_id=17,
        source_hash="b" * 64,
        object_type="goal",
        semantic_role="procedure_goal",
        scope_type="global",
        scope_entity_id=None,
        text="find callers",
    )
    first = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        on_unavailable="create",
        job_id=17,
        idempotency_key=key,
    )
    second = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        on_unavailable="create",
        job_id=17,
        idempotency_key=key,
    )

    assert first.action == "create"
    assert second.action == "create"
    assert second.reused_decision is True
    assert candidate_calls == 1
    assert judge.batch_calls == 1
    assert len(pool.inserts) == 1
    assert pool.inserts[0][13] == 17


@pytest.mark.asyncio
async def test_goal_same_replay_requires_a_live_scoped_goal(monkeypatch):
    pool = ReplayPool()
    pool.prior = {
        "id": "decision-1",
        "candidate_text": "find callers",
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "same",
        "resolved_id": "goal-1",
        "candidates": [{
            "id": "goal-1",
            "name": "find callers",
            "relation": "same",
            "confidence": 0.95,
        }],
        "judge_provider": "fake",
        "judge_model": "test",
        "fts_candidates": 1,
        "vector_candidates": 0,
        "detail": {},
    }
    judge = CountingJudge()

    async def candidates(*_args, **_kwargs):
        raise AssertionError("candidate generation must not run for a valid replay")

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    outcome = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        idempotency_key="replay-key",
    )

    assert outcome.action == "reuse"
    assert outcome.resolved_id == "goal-1"
    assert outcome.reused_decision is True
    assert judge.batch_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior_text,prior_scope",
    [("different text", "global"), ("find callers", "entity")],
)
async def test_goal_replay_rejects_mismatched_text_or_scope(
    monkeypatch, prior_text, prior_scope,
):
    pool = ReplayPool()
    pool.prior = {
        "id": "decision-1",
        "candidate_text": prior_text,
        "scope_type": prior_scope,
        "scope_entity_id": "other" if prior_scope == "entity" else None,
        "decision": "distinct",
        "resolved_id": None,
        "candidates": [],
        "judge_provider": "fake",
        "judge_model": "test",
        "fts_candidates": 0,
        "vector_candidates": 0,
        "detail": {},
    }
    judge = CountingJudge()
    candidate_calls = 0

    async def candidates(*_args, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return [Candidate("candidate-1", "existing", "existing goal")], 1, 0

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    with pytest.raises(PermanentIdentityConflict):
        await resolve_goal_identity(
            pool,
            name="find callers",
            description=None,
            scope_type="global",
            scope_entity_id=None,
            embedding=None,
            embedding_model=None,
            judge=judge,
            on_unavailable="create",
            idempotency_key="replay-key",
        )

    assert candidate_calls == 0
    assert judge.batch_calls == 0


@pytest.mark.asyncio
async def test_procedure_decision_replays_before_embedding_or_judging():
    pool = ReplayPool()
    name = "locate callers"
    goal = "find callers"
    steps = [{"description": "search the repository"}]
    text = procedure_text(name, goal, steps)
    pool.prior = {
        "id": "decision-1",
        "candidate_text": text,
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "distinct",
        "resolved_id": None,
        "candidates": [{"id": "procedure-1", "name": "old"}],
        "judge_provider": "fake",
        "judge_model": "test",
        "fts_candidates": 1,
        "vector_candidates": 0,
        "job_id": 17,
        "detail": {"goal_id": "goal-1"},
    }
    judge = CountingJudge()

    class Embedder:
        async def embed_one(self, *_args, **_kwargs):
            raise AssertionError("embedding must not run for a valid procedure replay")

    decision, resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name=name,
        goal_text=goal,
        steps=steps,
        source_key="source-1",
        scope_type="global",
        scope_entity_id=None,
        embedder=Embedder(),
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )

    assert decision == "distinct"
    assert resolved is None
    assert judge.single_calls == 0


@pytest.mark.asyncio
async def test_procedure_same_replay_requires_a_live_row_for_the_same_goal():
    pool = ReplayPool()
    name = "locate callers"
    goal = "find callers"
    steps = [{"description": "search the repository"}]
    text = procedure_text(name, goal, steps)
    pool.prior = {
        "id": "decision-1",
        "candidate_text": text,
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "same",
        "resolved_id": "procedure-1",
        "candidates": [{"id": "procedure-1", "name": name}],
        "judge_provider": "fake",
        "judge_model": "test",
        "fts_candidates": 1,
        "vector_candidates": 0,
        "job_id": 17,
        "detail": {"goal_id": "goal-1"},
    }
    judge = CountingJudge()

    class Embedder:
        async def embed_one(self, *_args, **_kwargs):
            raise AssertionError("embedding must not run for a valid procedure replay")

    decision, resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name=name,
        goal_text=goal,
        steps=steps,
        source_key="source-1",
        scope_type="global",
        scope_entity_id=None,
        embedder=Embedder(),
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )

    assert decision == "same"
    assert resolved == "procedure-1"
    assert judge.single_calls == 0


@pytest.mark.asyncio
async def test_legacy_process_pending_jobs_injects_claimed_identity(monkeypatch):
    from app.services import ingestion_jobs

    seen = []

    async def claim(_pool, **_kwargs):
        return [{
            "id": 77,
            "job_type": "test",
            "payload": {"_job": {"id": 999, "attempt": 99}},
            "attempts": 4,
        }]

    async def handler(_pool, payload):
        seen.append(payload)

    class Pool:
        async def execute(self, *_args):
            return "UPDATE 1"

    monkeypatch.setattr(ingestion_jobs, "claim_jobs", claim)
    monkeypatch.setitem(ingestion_jobs.JOB_HANDLERS, "test", handler)

    result = await ingestion_jobs._process_pending_jobs(
        Pool(), limit=1, job_types=None, worker_id="legacy"
    )

    assert result["done"] == 1
    assert seen[0]["_job"] == {"id": 77, "attempt": 4}


class ExplodingPool:
    async def fetchrow(self, *_args):
        raise AssertionError("invalid on_unavailable must be rejected before pool access")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", ["Raise", "create ", "unknown", 1])
async def test_goal_rejects_non_exact_on_unavailable_before_replay_or_model_work(bad_value):
    judge = CountingJudge()
    with pytest.raises(ValueError, match="on_unavailable"):
        await resolve_goal_identity(
            ExplodingPool(),
            name="find callers",
            description=None,
            scope_type="global",
            scope_entity_id=None,
            embedding=None,
            embedding_model=None,
            judge=judge,
            on_unavailable=bad_value,
            idempotency_key="existing-key",
        )
    assert judge.batch_calls == 0


@pytest.mark.asyncio
async def test_procedure_rejects_non_exact_on_unavailable_before_replay_or_model_work():
    judge = CountingJudge()
    with pytest.raises(ValueError, match="on_unavailable"):
        await resolve_procedure_identity(
            ExplodingPool(),
            goal_id="goal-1",
            name="locate callers",
            goal_text="find callers",
            steps=[],
            source_key="source-1",
            scope_type="global",
            scope_entity_id=None,
            embedder=None,
            judge=judge,
            on_unavailable="CREATE",
        )
    assert judge.single_calls == 0


def _goal_decision_row(*, candidate_text="find callers", job_id=None, decision="distinct",
                       resolved_id=None, candidates=None):
    return {
        "id": "decision-1",
        "idempotency_key": "existing-key",
        "candidate_text": candidate_text,
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": decision,
        "resolved_id": resolved_id,
        "candidates": [] if candidates is None else candidates,
        "judge_provider": "winner",
        "judge_model": "winner-model",
        "fts_candidates": 0,
        "vector_candidates": 0,
        "job_id": job_id,
        "detail": {},
    }


@pytest.mark.asyncio
async def test_goal_replay_canonicalizes_candidate_text_like_key_derivation(monkeypatch):
    pool = ReplayPool()
    pool.prior = _goal_decision_row(candidate_text="  FIND\tCALLERS  ")
    judge = CountingJudge()

    async def candidates(*_args, **_kwargs):
        raise AssertionError("valid replay must not generate candidates")

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    outcome = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        idempotency_key="existing-key",
    )

    assert outcome.action == "create"
    assert outcome.decision == "distinct"
    assert outcome.reused_decision is True
    assert judge.batch_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored_job_id", "caller_job_id"), [(None, 17), (17, None)])
async def test_job_namespaced_replay_requires_exact_job_identity(stored_job_id, caller_job_id):
    pool = ReplayPool()
    pool.prior = _goal_decision_row(job_id=stored_job_id)
    judge = CountingJudge()

    with pytest.raises(PermanentIdentityConflict):
        await resolve_goal_identity(
            pool,
            name="find callers",
            description=None,
            scope_type="global",
            scope_entity_id=None,
            embedding=None,
            embedding_model=None,
            judge=judge,
            job_id=caller_job_id,
            idempotency_key="existing-key",
        )
    assert judge.batch_calls == 0


@pytest.mark.asyncio
async def test_goal_judge_unavailable_replay_obeys_current_failure_policy():
    pool = ReplayPool()
    pool.prior = _goal_decision_row(decision="judge_unavailable")
    judge = CountingJudge()

    with pytest.raises(IdentityReplayError, match="on_unavailable"):
        await resolve_goal_identity(
            pool,
            name="find callers",
            description=None,
            scope_type="global",
            scope_entity_id=None,
            embedding=None,
            embedding_model=None,
            judge=judge,
            on_unavailable="raise",
            idempotency_key="existing-key",
        )
    assert judge.batch_calls == 0


@pytest.mark.asyncio
async def test_procedure_judge_unavailable_replay_obeys_current_failure_policy():
    pool = ReplayPool()
    text = procedure_text("locate callers", "find callers", [])
    pool.prior = {
        "id": "decision-1",
        "idempotency_key": "proc:source-1",
        "candidate_text": text,
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "judge_unavailable",
        "resolved_id": None,
        "candidates": [],
        "judge_provider": "fake",
        "judge_model": "test",
        "fts_candidates": 1,
        "vector_candidates": 0,
        "job_id": None,
        "detail": {"goal_id": "goal-1"},
    }
    judge = CountingJudge()

    with pytest.raises(IdentityReplayError, match="on_unavailable"):
        await resolve_procedure_identity(
            pool,
            goal_id="goal-1",
            name="locate callers",
            goal_text="find callers",
            steps=[],
            source_key="source-1",
            scope_type="global",
            scope_entity_id=None,
            embedder=None,
            judge=judge,
            on_unavailable="raise",
        )
    assert judge.single_calls == 0


@pytest.mark.asyncio
async def test_goal_no_candidates_is_persisted_and_replayed(monkeypatch):
    pool = ReplayPool()
    judge = CountingJudge()
    candidate_calls = 0

    async def candidates(*_args, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return [], 0, 0

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    first = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        job_id=17,
        idempotency_key="no-candidate-key",
    )
    second = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        job_id=17,
        idempotency_key="no-candidate-key",
    )

    assert first.decision == "no_candidates"
    assert second.decision == "no_candidates"
    assert second.reused_decision is True
    assert candidate_calls == 1
    assert judge.batch_calls == 0
    assert len(pool.inserts) == 1


@pytest.mark.asyncio
async def test_procedure_no_candidates_is_persisted_and_replayed(monkeypatch):
    pool = ReplayPool()
    judge = CountingJudge()
    candidate_calls = 0

    async def candidates(*_args, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return []

    monkeypatch.setattr(procedure_identity, "_candidates", candidates)
    decision, resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name="locate callers",
        goal_text="find callers",
        steps=[],
        source_key="source-no-candidates",
        scope_type="global",
        scope_entity_id=None,
        embedder=None,
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )
    replay_decision, replay_resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name="locate callers",
        goal_text="find callers",
        steps=[],
        source_key="source-no-candidates",
        scope_type="global",
        scope_entity_id=None,
        embedder=None,
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )

    assert decision == "no_candidates"
    assert resolved is None
    assert replay_decision == "no_candidates"
    assert replay_resolved is None
    assert candidate_calls == 1
    assert judge.single_calls == 0
    assert len(pool.inserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        _goal_decision_row(decision="bogus"),
        _goal_decision_row(decision="same", resolved_id="goal-1", candidates=[]),
        {**_goal_decision_row(), "candidates": "not-json"},
    ],
)
async def test_invalid_goal_replay_data_fails_closed(monkeypatch, row):
    pool = ReplayPool()
    pool.prior = row
    judge = CountingJudge()

    async def candidates(*_args, **_kwargs):
        raise AssertionError("invalid replay must not regenerate candidates")

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    with pytest.raises(IdentityReplayError):
        await resolve_goal_identity(
            pool,
            name="find callers",
            description=None,
            scope_type="global",
            scope_entity_id=None,
            embedding=None,
            embedding_model=None,
            judge=judge,
            idempotency_key="existing-key",
        )
    assert judge.batch_calls == 0


class ConflictPool:
    def __init__(self, winner):
        self.winner = winner
        self.insert_attempted = False

    async def fetchrow(self, sql, *params):
        normalized = " ".join(sql.split())
        if "FROM identity_decisions" in normalized:
            return self.winner if self.insert_attempted else None
        if "INSERT INTO identity_decisions" in normalized:
            self.insert_attempted = True
            return None
        return None

    async def fetchval(self, sql, *params):
        if "FROM goals" in " ".join(sql.split()):
            return 1
        return None


@pytest.mark.asyncio
async def test_goal_concurrent_insert_returns_complete_winner(monkeypatch):
    winner = _goal_decision_row(
        candidate_text="find callers",
        job_id=17,
        decision="distinct",
        candidates=[{"id": "winner-1", "name": "winner", "relation": "related"}],
    )
    winner["fts_candidates"] = 7
    winner["vector_candidates"] = 3
    pool = ConflictPool(winner)
    judge = CountingJudge()

    async def candidates(*_args, **_kwargs):
        return [Candidate("local-1", "local", "local goal")], 1, 0

    async def same_batch(*_args, **_kwargs):
        judge.batch_calls += 1
        return ChainResult(
            ok=True,
            value=[{"relation": "same", "confidence": 0.99}],
            provider="local",
            model="local-model",
        )

    monkeypatch.setattr(identity_resolution, "generate_goal_candidates", candidates)
    monkeypatch.setattr(judge, "judge_identity_batch", same_batch)
    outcome = await resolve_goal_identity(
        pool,
        name="find callers",
        description=None,
        scope_type="global",
        scope_entity_id=None,
        embedding=None,
        embedding_model=None,
        judge=judge,
        job_id=17,
        idempotency_key="existing-key",
    )

    assert outcome.decision == "distinct"
    assert outcome.action == "create"
    assert outcome.candidates[0].id == "winner-1"
    assert outcome.fts_candidates == 7
    assert outcome.vector_candidates == 3
    assert outcome.judge_provider == "winner"
    assert judge.batch_calls == 1


@pytest.mark.asyncio
async def test_procedure_concurrent_insert_returns_winner_decision(monkeypatch):
    name = "locate callers"
    goal = "find callers"
    steps = [{"description": "search"}]
    winner = {
        "id": "decision-winner",
        "idempotency_key": "proc:source-race",
        "candidate_text": procedure_text(name, goal, steps),
        "scope_type": "global",
        "scope_entity_id": None,
        "decision": "distinct",
        "resolved_id": None,
        "candidates": [{"id": "winner-procedure", "name": "winner"}],
        "judge_provider": "winner",
        "judge_model": "winner-model",
        "fts_candidates": 4,
        "vector_candidates": 2,
        "job_id": 17,
        "detail": {"goal_id": "goal-1"},
    }
    pool = ConflictPool(winner)
    judge = CountingJudge()

    async def candidates(*_args, **_kwargs):
        return [Candidate("local-procedure", "local", "local procedure")]

    async def same(_kind, _text, _candidate):
        judge.single_calls += 1
        return ChainResult(
            ok=True,
            value={"relation": "same", "confidence": 0.99},
            provider="local",
            model="local-model",
        )

    monkeypatch.setattr(procedure_identity, "_candidates", candidates)
    monkeypatch.setattr(judge, "judge_identity", same)
    decision, resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name=name,
        goal_text=goal,
        steps=steps,
        source_key="source-race",
        scope_type="global",
        scope_entity_id=None,
        embedder=None,
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )

    assert decision == "distinct"
    assert resolved is None
    assert judge.single_calls == 1


class VersionReplayPool:
    def __init__(self, latest_id, decision="new_version"):
        self.latest_id = latest_id
        self.prior = {
            "id": "decision-1",
            "idempotency_key": "proc:source-version",
            "candidate_text": "locate callers: find callers. Steps: search",
            "scope_type": "global",
            "scope_entity_id": None,
            "decision": decision,
            "resolved_id": "procedure-1",
            "candidates": [{"id": "procedure-1", "name": "old"}],
            "judge_provider": "fake",
            "judge_model": "test",
            "fts_candidates": 1,
            "vector_candidates": 0,
            "job_id": 17,
            "detail": {"goal_id": "goal-1"},
        }

    async def fetchrow(self, sql, *params):
        normalized = " ".join(sql.split())
        if "FROM identity_decisions" in normalized:
            return self.prior
        if "SELECT procedure_id::text AS procedure_id" in normalized:
            return {"procedure_id": "procedure-family-1"}
        if "SELECT id::text AS id FROM procedures" in normalized:
            return {"id": self.latest_id} if self.latest_id else None
        return None

    async def fetchval(self, sql, *params):
        if "FROM procedures" in " ".join(sql.split()):
            return 0
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_decision", ["same", "new_version"])
async def test_procedure_replay_follows_superseded_row_to_latest_live_version(stored_decision):
    pool = VersionReplayPool("procedure-2", decision=stored_decision)
    judge = CountingJudge()

    class Embedder:
        async def embed_one(self, *_args, **_kwargs):
            raise AssertionError("superseded replay must not embed")

    decision, resolved = await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name="locate callers",
        goal_text="find callers",
        steps=[{"description": "search"}],
        source_key="source-version",
        scope_type="global",
        scope_entity_id=None,
        embedder=Embedder(),
        judge=judge,
        on_unavailable="create",
        job_id=17,
    )

    assert decision == stored_decision
    assert resolved == "procedure-2"
    assert judge.single_calls == 0


@pytest.mark.asyncio
async def test_procedure_replay_does_not_use_dead_target_without_live_version():
    pool = VersionReplayPool(None)
    judge = CountingJudge()

    with pytest.raises(IdentityReplayError):
        await resolve_procedure_identity(
            pool,
            goal_id="goal-1",
            name="locate callers",
            goal_text="find callers",
            steps=[{"description": "search"}],
            source_key="source-version",
            scope_type="global",
            scope_entity_id=None,
            embedder=None,
            judge=judge,
            on_unavailable="create",
            job_id=17,
        )
    assert judge.single_calls == 0
