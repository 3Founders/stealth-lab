"""The DS-1000 round-4 fixes that need no database: the find_ways governor, which judged Goal candidates may
contribute related examples, and the single suggested candidate on an ambiguous answer."""
from __future__ import annotations

import json

import app.mcp_server.server as srv
from app.mcp_server.find_ways_governor import FindWaysGovernor, request_key
from app.services.retrieval_service import Hit, related_example_goals


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def gov(clock, **kw):
    return FindWaysGovernor(window_s=600, identical_limit=3, max_calls=5, min_words=3, clock=clock, **kw)


def test_identical_requests_are_cached_then_refused_as_a_loop():
    clock = Clock()
    g = gov(clock)
    q = "add a docx export to reports"
    d = g.check("s1", q, "")
    assert d.action == "run"
    g.remember("s1", d.key, json.dumps({"outcome": "resolved", "procedures": []}))
    second = g.check("s1", q.upper() + "  ", "")            # same request, different case/spacing
    assert second.action == "cached" and json.loads(second.reply)["governor"] == {"cached": True}
    assert g.check("s1", q, "").action == "cached"
    loop = g.check("s1", q, "")
    assert loop.action == "refuse" and json.loads(loop.reply)["governor"]["reason"] == "repeated_request"
    assert g.check("s2", q, "").action == "run"               # another session is unaffected
    clock.t += 601
    assert g.check("s1", q, "").action == "run"               # the window frees it again


def test_repo_facts_are_part_of_the_request():
    assert request_key("export docx files now", "CLAIM|R-001") != request_key("export docx files now", "CLAIM|R-002")


def test_budget_and_minimum_size():
    clock = Clock()
    g = gov(clock)
    for i in range(5):
        assert g.check("s", f"distinct request number {i} here", "").action == "run"
    over = g.check("s", "one more distinct request here", "")
    assert over.action == "refuse" and json.loads(over.reply)["governor"]["reason"] == "budget"
    small = g.check("t", "fix it", "")
    assert small.action == "refuse" and json.loads(small.reply)["governor"]["reason"] == "too_small"


def test_refusals_and_errors_are_never_cached():
    clock = Clock()
    g = gov(clock)
    d = g.check("s", "a real request here", "")
    g.remember("s", d.key, "REFUSED: something")
    assert g.check("s", "a real request here", "").action == "run"


def _hit(i, relation, conf, rrf=0.1, judged=True):
    return Hit(id=f"g{i}", name=f"goal {i}", text="", home_shard_id="control", rrf=rrf, relation=relation,
               confidence=conf, judged=judged)


def test_related_goals_keep_variants_and_drop_only_firm_unrelated():
    hits = [_hit(1, "unrelated", 0.95), _hit(2, "unrelated", 0.5, rrf=0.3), _hit(3, "partial", 0.7, rrf=0.1),
            _hit(4, "matches", 0.9, rrf=0.05), _hit(5, None, None, judged=False), _hit(2, "partial", 0.6)]
    kept = related_example_goals(hits, drop_confidence=0.8)
    assert [h.id for h in kept] == ["g4", "g2", "g3"]           # matches, then partial (by rank); g1 firm-unrelated dropped
    assert kept[1].relation == "partial"                         # best verdict per Goal wins


def test_suggested_candidate_is_the_best_scored_one_with_a_way():
    cands = [{"goal": {"id": "a", "canonical_name": "A"}, "score": 0.9, "ways": []},
             {"goal": {"id": "b", "canonical_name": "B"}, "score": 0.7,
              "ways": [{"procedure_id": "pb", "name": "way B", "verified_solution": {"code": "x"}}]},
             {"goal": {"id": "c", "canonical_name": "C"}, "score": 0.6, "ways": [{"procedure_id": "pc", "name": "way C"}]}]
    s = srv._suggested_candidate(cands)
    assert (s["goal_id"], s["procedure_id"], s["verified_solution"]["code"]) == ("b", "pb", "x")
    assert "not the same Goal" in s["how_to_use"]
    assert srv._suggested_candidate([{"goal": {"id": "a"}, "score": 1.0}]) is None
