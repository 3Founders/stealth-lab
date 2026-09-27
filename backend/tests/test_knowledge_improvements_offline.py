"""docs/knowledge_side_improvements.md, offline: verified examples (changes 1-2) and the
related-examples Goal filter (changes 3-4). Judge batch fixes (change 7): test_judge_batch_offline.py."""
from __future__ import annotations

from app.services.retrieval_service import Hit, related_example_goals
from app.services.verified_examples import MAX_CODE_CHARS, build_example, public_example


# ---------------------------------------------------------------- changes 1-2

def test_example_is_capped_redacted_and_rendered():
    secret = "sk-" + "A" * 40
    ex = build_example(task="Compute column percentages", code=f"key = '{secret}'\n" + "x = 1\n" * 10_000,
                       verified_by="tests")
    assert len(ex["code"]) <= MAX_CODE_CHARS + 64 and ex["truncated"] is True
    assert secret not in ex["code"]
    assert public_example(ex) == {"task": ex["task"], "code": ex["code"], "language": "python", "verified_by": "tests"}
    assert build_example(task="", code="x = 1") is None and build_example(task="t", code="  ") is None
    assert public_example({"task": "t"}) is None and public_example("not json") is None


# ---------------------------------------------------------------- changes 3-4

def _hit(i, relation, conf, rrf, judged=True):
    return Hit(id=f"g{i}", name=f"goal {i}", text="", home_shard_id="K000", rrf=rrf,
               relation=relation, confidence=conf, judged=judged)


def test_related_goals_drop_only_firm_unrelated_and_keep_close_variants():
    hits = [
        _hit(1, "unrelated", 1.0, 0.9),      # firmly unrelated: dropped
        _hit(2, "partial", 0.66, 0.5),       # broad domain Goal
        _hit(3, "unrelated", 0.49, 0.4),     # the close variant (problem 116): kept
        _hit(4, "matches", 0.95, 0.1),
        _hit(5, None, None, 0.99, judged=False),   # never judged against this request: not eligible
    ]
    kept = related_example_goals(hits, drop_confidence=0.8)
    assert [h.id for h in kept] == ["g4", "g2", "g3"]


def test_related_goals_dedupe_keeps_the_strongest_verdict():
    hits = [_hit(1, "unrelated", 0.5, 0.3), _hit(1, "partial", 0.7, 0.2)]
    assert [(h.id, h.relation) for h in related_example_goals(hits, drop_confidence=0.8)] == [("g1", "partial")]
