"""Offline: find_ways' contextual judge asks about a chunk of candidates per call (retrieval_service._judge_all), and a
chunk the batch call cannot answer is judged one candidate at a time -- never a heuristic, never dropped silently."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import retrieval_service as rs
from app.services.semantic import prompts


def _hits(n):
    return [rs.Hit(id=f"g{i}", name=f"goal {i}", text=f"goal {i}", home_shard_id="K001", rrf=1.0 / (i + 1)) for i in range(n)]


class _Judge:
    def __init__(self, *, batch_ok=True):
        self.batch_ok, self.batch_calls, self.single_calls = batch_ok, [], 0

    async def judge_identity_batch(self, kind, a, candidates):
        self.batch_calls.append(len(candidates))
        if not self.batch_ok:
            return SimpleNamespace(ok=False, value=None, provider=None)
        return SimpleNamespace(ok=True, provider="gemma",
                               value=[{"relation": "matches", "confidence": 0.9} for _ in candidates])

    async def judge_identity(self, kind, a, b):
        self.single_calls += 1
        return SimpleNamespace(ok=True, provider="gemma", value={"relation": "partial", "confidence": 0.7})


def _judge_all(judge, hits, cfg=rs.RetrievalConfig()):
    meta = rs.RetrievalMeta()
    asyncio.run(rs._judge_all(judge, "task_goal", "ctx", hits, cfg, meta, stage="goal"))
    return meta


def test_candidates_are_judged_in_chunks_not_one_call_each():
    judge, hits = _Judge(), _hits(16)
    meta = _judge_all(judge, hits)
    assert judge.batch_calls == [10, 6] and judge.single_calls == 0
    assert all(h.judged and h.relation == "matches" for h in hits)
    assert meta.counts["goal_judged"] == 16 and meta.counts["goal_batched"] == 16 and meta.providers == ["gemma"]


def test_a_failed_batch_falls_back_to_one_call_per_candidate():
    judge, hits = _Judge(batch_ok=False), _hits(3)
    meta = _judge_all(judge, hits)
    assert judge.batch_calls == [3] and judge.single_calls == 3
    assert all(h.judged and h.relation == "partial" for h in hits) and meta.counts["goal_judged"] == 3


def test_batch_size_one_and_a_lone_candidate_keep_the_single_call_path():
    judge = _Judge()
    _judge_all(judge, _hits(3), rs.replace_cfg(rs.RetrievalConfig(), judge_batch_size=1))
    _judge_all(judge, _hits(1))
    assert judge.batch_calls == [] and judge.single_calls == 4


def test_the_task_batch_prompts_keep_the_task_framing():
    assert "user's TASK" in prompts.IDENTITY_BATCH_SYSTEM_PROMPTS["task_goal"]
    assert "PROCEDURE" in prompts.IDENTITY_BATCH_SYSTEM_PROMPTS["task_procedure"]
    assert prompts.IDENTITY_BATCH_SYSTEM_PROMPTS["goal"].startswith("You compare one new goal A")
