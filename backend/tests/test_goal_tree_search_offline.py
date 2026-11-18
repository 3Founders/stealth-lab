"""The task lookup's Goal tree walk, without a database or a model (app/services/goal_search_tree.py).

The case this pins is the one the flat search could not answer: a request a *narrower* Goal serves better than the
broad Goal that also mentions it. "Survey the cancer genomics literature and propose an experiment" is matched by
"Research any topic", and the biology Goal is written so small a Goal that its text never wins the flat search. The
walk must find it -- and only when JEV says to go down, one level at a time.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import goal_search_tree as tree


class ChainResult:
    def __init__(self, value, provider="jev", model="system-one"):
        self.ok = value is not None
        self.value = value
        self.provider = provider
        self.model = model


class FakeJEV:
    """Answers by the Goal's name. `descend` says whether a narrower goal of that Goal belongs to the request."""

    def __init__(self, matches: dict[str, str], descend: dict[str, str], confidence: float = 0.9):
        self.matches = matches            # goal name -> "matches" | "partial" | "unrelated"
        self.descend = descend            # goal name -> "matches" | "partial" | "unrelated"
        self.confidence = confidence
        self.calls: list[tuple[str, str]] = []

    async def judge_identity(self, kind, a, b):
        assert kind == "task_goal"
        self.calls.append((a, b))
        descend = "more specific goal" in b
        name = b.split("\n")[0].split(":")[0].strip()
        relation = (self.descend if descend else self.matches).get(name)
        if relation is None:
            return ChainResult(None)
        return ChainResult({"relation": relation, "confidence": self.confidence})


def _children(table: dict[str, list[str]]):
    async def read(goal_ids):
        return {g: [{"goal_id": c, "canonical_name": c, "text": f"{c}: biology", "score": 1.0 - i / 10}
                    for i, c in enumerate(table.get(g, []))] for g in goal_ids}
    return read


def _anchor(name: str, score: float = 0.9):
    # As the search returns it: admitted by its own judgment, the descent question not yet asked.
    return tree.Node(goal_id=name, name=name, depth=0, score=score, text=f"{name}: any topic",
                     verdict=tree.Verdict(admitted=True, relation="matches", confidence=score, via="search"))


def run(coro):
    return asyncio.run(coro)


def test_a_narrower_goal_is_found_only_when_jev_says_to_go_down():
    jev = FakeJEV(
        matches={"Research any topic": "matches", "Survey academic literature": "partial",
                 "Survey cancer genomics literature": "matches", "Design a wet experiment": "unrelated"},
        descend={"Research any topic": "matches", "Survey academic literature": "matches",
                 "Survey cancer genomics literature": "unrelated"},
    )
    children = _children({
        "Research any topic": ["Survey academic literature", "Design a wet experiment"],
        "Survey academic literature": ["Survey cancer genomics literature"],
    })
    walk = run(tree.descend([_anchor("Research any topic")], children, _transcode(jev), "survey cancer genomics"))

    names = [n.name for n in walk.nodes]
    assert walk.as_dict()["mode"] == "tree"
    assert names[0] == "Research any topic"                     # the search's own Goal stays first
    assert "Survey cancer genomics literature" in names         # the narrow Goal the flat search could not see
    assert "Design a wet experiment" not in names               # JEV called it unrelated: never returned
    assert [n.depth for n in walk.nodes if n.name == "Survey cancer genomics literature"] == [2]
    assert walk.stopped == "judge_did_not_descend"               # it went down exactly as far as JEV allowed


def test_without_a_judge_the_answer_is_exactly_the_flat_one():
    walk = run(tree.descend([_anchor("Research any topic")], _children({}), None, "anything"))
    assert walk.as_dict()["mode"] == "flat" and walk.goal_ids == ["Research any topic"]
    assert walk.stopped == "no_judge"


def test_a_level_the_judge_does_not_answer_is_not_descended():
    class Silent:
        async def judge_identity(self, kind, a, b):
            return ChainResult(None)

    walk = run(tree.descend([_anchor("Research any topic")], _children({"Research any topic": ["Child"]}),
                            _transcode(Silent()), "anything"))
    # No answer even for the anchor's descent: nothing below it is reached.
    assert walk.goal_ids == ["Research any topic"] and walk.stopped == "judge_did_not_descend"


def test_a_child_is_admitted_on_its_own_judgment_not_on_its_parents():
    jev = FakeJEV(
        matches={"Research any topic": "matches", "Under a good goal": "unrelated"},
        descend={"Research any topic": "matches"},
    )
    walk = run(tree.descend([_anchor("Research any topic")],
                            _children({"Research any topic": ["Under a good goal"]}), _transcode(jev), "q"))
    # A Goal under a Goal JEV liked is still judged on its own; if it does not serve the request it stays out.
    assert "Under a good goal" not in [n.name for n in walk.nodes]


def test_a_weak_partial_is_not_kept_and_not_descended():
    # At confidence 0.5 a "partial" is below the firm bar: it serves the request only weakly, so it is not returned
    # and the walk does not spend the judge on its children.
    jev = FakeJEV(
        matches={"Research any topic": "matches", "Maybe literature": "partial"},
        descend={"Research any topic": "matches"},
        confidence=0.5,
    )
    walk = run(tree.descend([_anchor("Research any topic")],
                            _children({"Research any topic": ["Maybe literature"]}), _transcode(jev), "q"))
    assert "Maybe literature" not in [n.name for n in walk.nodes]
    assert walk.stopped == "judge_did_not_descend"


def test_a_firm_partial_is_kept_and_descends_only_when_jev_says_so():
    jev = FakeJEV(
        matches={"Research any topic": "matches", "Partial but firm": "partial"},
        descend={"Research any topic": "matches", "Partial but firm": "unrelated"},
        confidence=0.8,
    )
    walk = run(tree.descend([_anchor("Research any topic")],
                            _children({"Research any topic": ["Partial but firm"]}), _transcode(jev), "q"))
    node = next(n for n in walk.nodes if n.name == "Partial but firm")
    assert node.admitted and not node.verdict.descend


def test_depth_fanout_and_goal_caps_hold():
    jev = FakeJEV(matches={f"G{i}": "matches" for i in range(50)},
                  descend={f"G{i}": "matches" for i in range(50)})
    table = {f"G{i}": [f"G{i + 1}"] for i in range(50)}
    walk = run(tree.descend([_anchor("G0")], _children(table), _transcode(jev), "q", max_depth=2))
    assert [n.depth for n in walk.nodes] == [0, 1, 2]
    assert walk.stopped == "max_depth"


def test_the_goal_cap_stops_the_walk():
    jev = FakeJEV(matches={f"G{i}": "matches" for i in range(30)},
                  descend={f"G{i}": "matches" for i in range(30)})
    table = {"G0": [f"G{i}" for i in range(1, 20)]}
    walk = run(tree.descend([_anchor("G0")], _children(table), _transcode(jev), "q", goals_per_level=20,
                            max_goals=5))
    assert len(walk.nodes) <= 5 and walk.stopped == "goal_limit"


def test_decision_reads_the_two_answers_and_fails_closed():
    assert tree.decide({"relation": "matches", "confidence": 0.8}, {"relation": "partial", "confidence": 0.75}) == tree.Verdict(
        admitted=True, descend=True, relation="matches", confidence=0.8, descend_confidence=0.75, via="")
    assert not tree.decide({"relation": "partial", "confidence": 0.3}, None).admitted
    assert not tree.decide(None, None).answered
    assert not tree.decide({"relation": "unrelated", "confidence": 0.99}, None).admitted
    assert not tree.decide({"relation": "matches", "confidence": 0.9}, {"relation": "partial", "confidence": 0.1}).descend


def _transcode(jev):
    """The walk's judge interface on top of a `judge_identity`-shaped fake (a dict answer, read by `decide`)."""
    from app.services import goal_tree_search as gts

    return gts.jev_judge(jev)
