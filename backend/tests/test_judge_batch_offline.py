"""Semantic judge batch path (docs/knowledge_side_improvements.md, change 7): JEV identity batches are
split into size-bounded chunks (System One returns HTTP 400 above ~170k characters) and merged in
candidate order; replies wrapped in prose or an unclosed fence still parse."""
from __future__ import annotations

import asyncio

import pytest

from app.services.semantic import prompts
from app.services.semantic.providers import JEVProvider, _size_bounded_chunks


def _cands(n: int, chars: int = 20) -> list[dict]:
    return [{"id": str(i), "text": f"goal {i} " + "x" * chars} for i in range(n)]


def test_chunks_respect_size_and_count_and_keep_order():
    cands = _cands(100, chars=1500)
    chunks = _size_bounded_chunks("goal", "a", cands, 60_000, 40)
    assert [c["id"] for chunk in chunks for c in chunk] == [c["id"] for c in cands]
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert all(len(prompts.build_identity_batch_user("goal", "a", chunk)) <= 60_000 for chunk in chunks)
    assert len(chunks) > 1
    huge = [{"id": "0", "text": "y" * 100_000}]
    assert _size_bounded_chunks("goal", "a", huge, 60_000, 40) == [huge]


class _Remote:
    model = "jev-test"

    def __init__(self):
        self.calls: list[int] = []

    async def systemone(self, user, questions):
        self.calls.append(len(questions))
        return {k: {"choice": "distinct", "confidence": 0.9} for k in questions}


def test_jev_identity_batch_splits_and_merges_in_candidate_order():
    remote = _Remote()
    jev = JEVProvider(remote, {"identity"})
    verdicts = asyncio.run(jev.identity_batch("goal", "a", _cands(95)))
    assert len(verdicts) == 95 and all(v["relation"] == "distinct" for v in verdicts)
    assert remote.calls == [40, 40, 15]


def test_reply_parser_accepts_an_object_wrapped_in_prose_or_an_open_fence():
    assert prompts._loads_object('Here you go:\n{"verdicts": []}\nThanks') == {"verdicts": []}
    assert prompts._loads_object('```json\n{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        prompts._loads_object("no json here")
    with pytest.raises(ValueError):
        prompts._loads_object("[1, 2]")
