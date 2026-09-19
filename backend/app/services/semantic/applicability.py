"""ApplicabilityJudge-protocol adapter over the shared SemanticJudge chain."""
from __future__ import annotations

from typing import Optional

from app.services.semantic.chain import ChainResult, SemanticJudge


class ChainedApplicabilityJudge:
    """Drop-in for the existing `ApplicabilityJudge` protocol
    (`judge_batch(goal, candidates)`). On an exhausted chain it RAISES
    SemanticJudgmentUnavailable -- it never returns UNKNOWN placeholders --
    so claim_conditioned_retrieval can mark the request PENDING and requeue.

    `model`/`model_version` identify the CHAIN CONFIGURATION for the judgment
    cache key (a different provider order/model set = different key); each
    judgment's own `.model` records which provider actually produced it."""

    def __init__(self, judge: SemanticJudge):
        self.judge = judge
        self.model = "semantic-chain"
        self.last_result: Optional[ChainResult] = None
        self.results: list[ChainResult] = []

    @classmethod
    def from_settings(cls, settings=None, **kw) -> "ChainedApplicabilityJudge":
        return cls(SemanticJudge.from_settings(settings, **kw))

    @property
    def model_version(self) -> str:
        return self.judge.chain_id

    async def judge_batch(self, goal, candidates):
        result = await self.judge.judge_applicability(goal, candidates)
        self.last_result = result
        self.results.append(result)
        return result.unwrap()
