"""Batched Procedure identity judging.

Procedure dedup is the highest-volume identity call in ingestion (one per
procedure source), so the judge is asked about the WHOLE RRF-fused candidate
list in a single ``judge_identity_batch`` provider call instead of N
``judge_identity`` round trips. These tests pin the parts that must not drift
while batching:

* one provider call for five candidates, and no per-pair call at all,
* the recorded candidate order is the RRF-fused order that was judged,
* outcome selection is unchanged: first qualifying candidate wins, the
  ``same``/``refinement`` -> ``same``/``new_version`` mapping holds, and
  ``SAME_MIN_CONFIDENCE`` is still a disqualifier rather than a score,
* an unusable batch contract is a hard error, never a silent per-pair
  fallback and never a guessed ``distinct``,
* the recorded decision replays without a second judgment.
"""
from __future__ import annotations

import pytest

from app.services import procedure_identity
from app.services.identity_resolution import (
    SAME_MIN_CONFIDENCE,
    Candidate,
)
from app.services.procedure_identity import procedure_text, resolve_procedure_identity
from app.services.semantic.chain import ChainResult, SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.semantic.policy import RetryPolicy, SemanticMetrics
from app.services.semantic.providers import ALL_CAPS, SemanticProvider

NAME = "locate callers"
GOAL = "find callers"
STEPS = [{"description": "search"}]
TEXT = procedure_text(NAME, GOAL, STEPS)
JOB_ID = 17
CANDIDATE_IDS = ["p-1", "p-2", "p-3", "p-4", "p-5"]


def fused_candidates(n: int = 5) -> list[Candidate]:
    return [
        Candidate(f"p-{i + 1}", f"candidate {i + 1}", f"candidate {i + 1}: existing procedure {i + 1}")
        for i in range(n)
    ]


class BatchPool:
    def __init__(self, latest_id: str = "p-1"):
        self.latest_id = latest_id
        self.prior = None
        self.inserts: list[tuple] = []

    async def fetchrow(self, sql, *params):
        normalized = " ".join(sql.split())
        if "FROM identity_decisions" in normalized:
            return self.prior
        if "INSERT INTO identity_decisions" in normalized:
            self.inserts.append(params)
            self.prior = {
                "id": "decision-1",
                "idempotency_key": params[14],
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
                "detail": params[15],
            }
            return {"id": "decision-1"}
        if "SELECT procedure_id::text AS procedure_id" in normalized:
            return {"procedure_id": "procedure-family-1"}
        if "SELECT id::text AS id FROM procedures" in normalized:
            return {"id": self.latest_id} if self.latest_id else None
        return None

    async def fetchval(self, sql, *params):
        if "FROM procedures" in " ".join(sql.split()):
            return 1
        return None


class BatchProvider(SemanticProvider):
    """Counts provider operations: ``identity_batch`` once, ``identity`` never."""

    def __init__(self, verdicts=None, *, name="batchy"):
        self.name, self.model = name, f"{name}-model"
        self.capabilities = frozenset(ALL_CAPS)
        self.verdicts = verdicts
        self.ops: list[tuple] = []
        self.batch_inputs: list[list[str]] = []

    def _verdicts(self, candidates):
        if self.verdicts is None:
            return [{"relation": "distinct", "confidence": 0.99} for _ in candidates]
        return [dict(v) for v in self.verdicts]

    async def identity_batch(self, kind, a, candidates):
        self.ops.append((kind, a, tuple(candidates)))
        self.batch_inputs.append([str(c) for c in candidates])
        return self._verdicts(candidates)

    async def identity(self, kind, a, b):
        self.ops.append((kind, a, b))
        return {"relation": "same", "confidence": 1.0}


class PairOnlyProvider(BatchProvider):
    """A provider that predates the batch contract; it must never be asked pairwise."""

    identity_batch = None


class PairOnlyJudge:
    """A judge wired to the pair contract only."""

    providers = ("pair-only",)
    chain_id = "pair-only:test"

    def __init__(self):
        self.pair_calls = 0

    async def judge_identity(self, kind, a, b):
        self.pair_calls += 1
        return {"relation": "same", "confidence": 1.0}


class StubJudge:
    def __init__(self, result, *, provider="stub", model="stub-model"):
        self.providers = ("stub",)
        self.chain_id = "stub:test"
        self.result = result
        self.provider, self.model = provider, model
        self.batch_calls = 0
        self.pair_calls = 0
        self.seen: list[list[str]] = []

    async def judge_identity_batch(self, kind, text, candidates):
        self.batch_calls += 1
        self.seen.append(list(candidates))
        return self.result

    async def judge_identity(self, kind, text, candidate):
        self.pair_calls += 1
        return self.result


def make_judge(*providers, attempts: int = 1) -> SemanticJudge:
    async def no_sleep(_s: float) -> None:
        return None

    return SemanticJudge(
        list(providers),
        RetryPolicy(per_provider_attempts=attempts, backoff_base_s=0.0, backoff_max_s=0.0, timeout_s=5.0),
        SemanticMetrics(),
        sleep=no_sleep,
        rng=lambda: 0.5,
    )


def stub_result(ok, value=None, reason="", provider="stub", model="stub-model"):
    return ChainResult(ok=ok, value=value, provider=provider, model=model, reason=reason)


async def run(pool, judge, *, source_key="src-batch", on_unavailable="create"):
    return await resolve_procedure_identity(
        pool,
        goal_id="goal-1",
        name=NAME,
        goal_text=GOAL,
        steps=STEPS,
        source_key=source_key,
        scope_type="global",
        scope_entity_id=None,
        embedder=None,
        judge=judge,
        on_unavailable=on_unavailable,
        job_id=JOB_ID,
    )


def patch_candidates(monkeypatch, cands):
    async def candidates(*_args, **_kwargs):
        return list(cands)

    monkeypatch.setattr(procedure_identity, "_candidates", candidates)


@pytest.mark.asyncio
async def test_five_candidates_cost_one_provider_call(monkeypatch):
    pool = BatchPool()
    provider = BatchProvider()
    patch_candidates(monkeypatch, fused_candidates(5))

    decision, resolved = await run(pool, make_judge(provider))

    assert decision == "distinct"
    assert resolved is None
    assert len(provider.ops) == 1
    assert provider.ops[0][0] == "procedure"
    assert len(provider.batch_inputs[0]) == 5
    assert provider.batch_inputs[0] == [c.text for c in fused_candidates(5)]
    assert len(pool.inserts) == 1


@pytest.mark.asyncio
async def test_candidate_order_is_recorded_in_judged_order(monkeypatch):
    pool = BatchPool()
    cands = fused_candidates(5)
    cands[0].fts_rank, cands[1].vec_rank = 1, 1
    provider = BatchProvider(
        [
            {"relation": "related", "confidence": 0.4},
            {"relation": "same", "confidence": 0.9},
            {"relation": "distinct", "confidence": 0.99},
            {"relation": "distinct", "confidence": 0.99},
            {"relation": "distinct", "confidence": 0.99},
        ]
    )
    patch_candidates(monkeypatch, cands)

    decision, resolved = await run(pool, make_judge(provider))

    assert decision == "same"
    assert resolved == "p-2"
    assert [c["id"] for c in pool.inserts[0][6]] == CANDIDATE_IDS
    recorded = pool.inserts[0][6]
    assert [(c["id"], c.get("relation"), c.get("confidence")) for c in recorded] == [
        ("p-1", "related", 0.4),
        ("p-2", "same", 0.9),
        ("p-3", None, None),
        ("p-4", None, None),
        ("p-5", None, None),
    ]
    assert pool.inserts[0][4] == "same" and pool.inserts[0][5] == "p-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdicts, expected_decision, expected_resolved",
    [
        pytest.param(
            [{"relation": "distinct", "confidence": 0.99}] * 5, "distinct", None, id="all-distinct"
        ),
        pytest.param(
            [{"relation": "same", "confidence": 0.99}, {"relation": "same", "confidence": 0.99},
             {"relation": "same", "confidence": 0.99}, {"relation": "same", "confidence": 0.99},
             {"relation": "same", "confidence": 0.99}],
            "same", "p-1", id="first-same-wins",
        ),
        pytest.param(
            [{"relation": "distinct", "confidence": 0.99}, {"relation": "distinct", "confidence": 0.99},
             {"relation": "same", "confidence": 0.99}, {"relation": "same", "confidence": 0.99},
             {"relation": "same", "confidence": 0.99}],
            "same", "p-3", id="first-qualifying-same",
        ),
        pytest.param(
            [{"relation": "distinct", "confidence": 0.99}, {"relation": "refinement", "confidence": 0.9},
             {"relation": "same", "confidence": 0.99}, {"relation": "distinct", "confidence": 0.99},
             {"relation": "distinct", "confidence": 0.99}],
            "new_version", "p-2", id="refinement-precedes-later-same",
        ),
        pytest.param(
            [{"relation": "same", "confidence": SAME_MIN_CONFIDENCE}, {"relation": "distinct", "confidence": 0.99},
             {"relation": "distinct", "confidence": 0.99}, {"relation": "distinct", "confidence": 0.99},
             {"relation": "distinct", "confidence": 0.99}],
            "same", "p-1", id="threshold-is-inclusive",
        ),
        pytest.param(
            [{"relation": "same", "confidence": 0.74}, {"relation": "refinement", "confidence": 0.74},
             {"relation": "same", "confidence": 0.74}, {"relation": "refinement", "confidence": 0.74},
             {"relation": "same", "confidence": 0.74}],
            "distinct", None, id="below-threshold-is-not-a-match",
        ),
        pytest.param(
            [{"relation": "related", "confidence": 0.99}, {"relation": "contradicts", "confidence": 0.99},
             {"relation": "distinct", "confidence": 0.99}, {"relation": "related", "confidence": 0.99},
             {"relation": "distinct", "confidence": 0.99}],
            "distinct", None, id="non-matching-relations",
        ),
    ],
)
async def test_outcome_selection_is_unchanged(monkeypatch, verdicts, expected_decision, expected_resolved):
    pool = BatchPool()
    provider = BatchProvider(verdicts)
    patch_candidates(monkeypatch, fused_candidates(5))

    decision, resolved = await run(pool, make_judge(provider))

    assert decision == expected_decision
    assert resolved == expected_resolved
    assert len(provider.ops) == 1
    assert pool.inserts[0][4] == expected_decision
    assert pool.inserts[0][5] == expected_resolved


@pytest.mark.asyncio
async def test_judge_unavailable_is_recorded_not_guessed(monkeypatch):
    pool = BatchPool()
    judge = StubJudge(stub_result(False, reason="all semantic providers failed"))
    patch_candidates(monkeypatch, fused_candidates(5))

    decision, resolved = await run(pool, judge)

    assert decision == "judge_unavailable"
    assert resolved is None
    assert judge.batch_calls == 1
    assert judge.pair_calls == 0
    assert pool.inserts[0][4] == "judge_unavailable"
    assert pool.inserts[0][5] is None
    assert [c.get("relation") for c in pool.inserts[0][6]] == [None] * 5


@pytest.mark.asyncio
async def test_judge_unavailable_raises_under_raise_policy(monkeypatch):
    pool = BatchPool()
    judge = StubJudge(stub_result(False, reason="all semantic providers failed"))
    patch_candidates(monkeypatch, fused_candidates(5))

    with pytest.raises(SemanticJudgmentUnavailable):
        await run(pool, judge, on_unavailable="raise")

    assert pool.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdicts", [[], [{"relation": "distinct", "confidence": 0.9}]])
async def test_wrong_verdict_count_is_unavailable_never_distinct(monkeypatch, verdicts):
    pool = BatchPool()
    judge = StubJudge(stub_result(True, value=verdicts, provider="stub", model="stub-model"))
    patch_candidates(monkeypatch, fused_candidates(5))

    decision, resolved = await run(pool, judge)

    assert decision == "judge_unavailable"
    assert resolved is None
    assert pool.inserts[0][4] == "judge_unavailable"

    with pytest.raises(SemanticJudgmentUnavailable):
        await run(BatchPool(), StubJudge(stub_result(True, value=verdicts)), on_unavailable="raise")


@pytest.mark.asyncio
async def test_judge_without_the_batch_contract_is_a_contract_error(monkeypatch):
    pool = BatchPool()
    judge = PairOnlyJudge()
    patch_candidates(monkeypatch, fused_candidates(5))

    with pytest.raises(SemanticJudgmentUnavailable) as excinfo:
        await run(pool, judge, on_unavailable="create")

    assert "judge_identity_batch" in str(excinfo.value)
    assert judge.pair_calls == 0
    assert pool.inserts == []


@pytest.mark.asyncio
async def test_provider_without_identity_batch_never_degrades_to_pairs(monkeypatch):
    pool = BatchPool()
    provider = PairOnlyProvider()
    patch_candidates(monkeypatch, fused_candidates(5))

    decision, resolved = await run(pool, make_judge(provider))

    assert decision == "judge_unavailable"
    assert resolved is None
    assert provider.ops == []
    assert pool.inserts[0][4] == "judge_unavailable"


@pytest.mark.asyncio
async def test_recorded_decision_replays_without_a_second_judgment(monkeypatch):
    pool = BatchPool(latest_id="p-2")
    provider = BatchProvider(
        [{"relation": "distinct", "confidence": 0.99}, {"relation": "same", "confidence": 0.9},
         {"relation": "same", "confidence": 0.9}, {"relation": "distinct", "confidence": 0.99},
         {"relation": "distinct", "confidence": 0.99}]
    )
    patch_candidates(monkeypatch, fused_candidates(5))
    judge = make_judge(provider)

    first = await run(pool, judge)
    second = await run(pool, judge)

    assert first == ("same", "p-2")
    assert second == ("same", "p-2")
    assert len(provider.ops) == 1
    assert len(pool.inserts) == 1
