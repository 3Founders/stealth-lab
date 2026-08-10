"""
Offline tests for the parts of the loop that don't need a database.

These cover the logic most likely to be wrong in ways that are hard to
see by reading: convergence detection, supporter counting, illegal state
transitions, and the fail-closed behaviour of the Layer 1 gate.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.debate.engine import DebateEngine
from app.debate.panel import MockAgent, _extract_json, assert_heterogeneous
from app.debate.state_machine import IllegalTransition, assert_transition, can_transition
from app.eval.layer1 import JudgeNotIndependent, Layer1Evaluator, enforce_independence
from app.models.change import ChangeSet
from app.models.debate import Candidate, Citation

TASK_ID = str(uuid4())


def _propose(summary="cache the extraction step", field="latency_estimate_ms", value=500):
    return json.dumps({
        "action": "propose",
        "summary": summary,
        "content": f"The step is slow; {summary}.",
        "cites": [{"node_id": TASK_ID, "node_table": "task_nodes"}],
        "change_set": {"ops": [{
            "op_type": "update_task_node", "task_node_id": TASK_ID,
            "changes": {field: value}, "reason": "reduce latency",
        }]},
    })


def _pass():
    return json.dumps({"action": "pass", "content": "nothing to add"})


def _amend(candidate_id):
    return json.dumps({
        "action": "amend", "candidate_id": str(candidate_id),
        "content": "agreed, tightening the estimate",
        "change_set": {"ops": [{
            "op_type": "update_task_node", "task_node_id": TASK_ID,
            "changes": {"latency_estimate_ms": 400}, "reason": "refined",
        }]},
    })


def _amend_missing_id():
    """An amend with candidate_id omitted entirely -- the real shape seen
    in a live run (candidate_id came back as None despite a candidate
    being clearly on the table)."""
    return json.dumps({
        "action": "amend",
        "content": "agreed, tightening the estimate",
        "change_set": {"ops": [{
            "op_type": "update_task_node", "task_node_id": TASK_ID,
            "changes": {"latency_estimate_ms": 400}, "reason": "refined",
        }]},
    })


def _mock_panel(scripts):
    return [
        MockAgent(agent_id=f"p{i}", responses=s, family=f"fam{i}")
        for i, s in enumerate(scripts)
    ]


def test_json_extraction_survives_fences_and_prose():
    assert _extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert _extract_json('Sure! {"a": 2} hope that helps')["a"] == 2
    with pytest.raises(ValueError):
        _extract_json("no json here at all")
    with pytest.raises(ValueError):
        _extract_json("[1,2,3]")  # array, not object


def test_heterogeneity_is_enforced():
    same = [MockAgent(agent_id="a", responses=[], family="x"),
            MockAgent(agent_id="b", responses=[], family="x")]
    with pytest.raises(ValueError, match="not heterogeneous"):
        assert_heterogeneous(same)
    assert_heterogeneous(_mock_panel([[], []]))  # distinct families: fine


def test_debate_converges_when_a_full_round_passes():
    """A round where nobody proposes or amends means the panel is done."""
    panel = _mock_panel([[_propose(), _pass()], [_pass(), _pass()]])
    engine = DebateEngine(panel, max_rounds=5)
    result = asyncio.run(engine.run(uuid4(), uuid4(), {"metric": "latency"}))
    assert result.termination_reason == "converged"
    assert result.rounds_used == 2  # round 1 proposed, round 2 was silent
    assert len(result.candidates) == 1


def test_debate_stops_at_round_cap():
    """Agents that keep proposing must still be bounded."""
    panel = _mock_panel([[_propose() for _ in range(10)],
                         [_propose() for _ in range(10)]])
    engine = DebateEngine(panel, max_rounds=3)
    result = asyncio.run(engine.run(uuid4(), uuid4(), {}))
    assert result.termination_reason == "round_cap"
    assert result.rounds_used == 3


def test_no_candidates_is_distinct_from_convergence():
    panel = _mock_panel([[_pass()], [_pass()]])
    result = asyncio.run(DebateEngine(panel).run(uuid4(), uuid4(), {}))
    assert result.termination_reason == "no_candidates"
    assert result.candidates == []


def test_amend_adds_a_supporter_and_gates_eval():
    """Section 7: >=2 supporters to reach eval. One proposal alone shouldn't."""
    p0 = MockAgent(agent_id="p0", responses=[_propose(), _pass()], family="a")
    engine = DebateEngine([p0], max_rounds=2, enforce_heterogeneity=False)
    result = asyncio.run(engine.run(uuid4(), uuid4(), {}))
    assert len(result.candidates) == 1
    assert result.eligible_candidates(min_supporters=2) == []
    assert len(result.eligible_candidates(min_supporters=1)) == 1


def test_amending_unknown_candidate_is_recorded_as_pass():
    """A malformed amend must not invent a candidate or crash the round."""
    panel = _mock_panel([[_amend(uuid4()), _pass()], [_pass(), _pass()]])
    result = asyncio.run(DebateEngine(panel).run(uuid4(), uuid4(), {}))
    assert result.candidates == []
    assert result.turns[0].action == "pass"


def test_amend_with_missing_id_recovers_when_exactly_one_candidate_exists():
    """
    Real failure this guards against: a live run had an agent send
    candidate_id=None with exactly one candidate on the table, and the
    turn's content was clearly building on it. With only one candidate,
    "amend" is unambiguous even without a usable id -- recover it rather
    than discarding a real contribution as a pass.
    """
    p0 = MockAgent(agent_id="p0", responses=[_propose(), _pass()], family="a")
    p1 = MockAgent(agent_id="p1", responses=[_amend_missing_id(), _pass()], family="b")
    engine = DebateEngine([p0, p1], max_rounds=2, enforce_heterogeneity=False)
    result = asyncio.run(engine.run(uuid4(), uuid4(), {}))

    assert len(result.candidates) == 1, "must not have invented a second candidate"
    only_candidate = result.candidates[0]
    assert "p1" in only_candidate.supporters, "the amend must have actually landed on the real candidate"
    round1_amend_turn = [t for t in result.turns if t.speaker_id == "p1" and t.round_number == 1][0]
    assert round1_amend_turn.action == "amend", "recovered amend must be recorded as amend, not silently downgraded"
    assert round1_amend_turn.candidate_id == only_candidate.id


def test_amend_with_missing_id_does_not_guess_among_multiple_candidates():
    """
    The recovery above is deliberately narrow: with 2+ live candidates,
    a missing/invalid candidate_id genuinely cannot be resolved --
    guessing which one was meant would be worse than discarding the
    turn. Must still fall back to "pass", not silently attach to
    whichever candidate happens to be first/last.
    """
    p0 = MockAgent(agent_id="p0", responses=[_propose(summary="option A"), _amend_missing_id()], family="a")
    p1 = MockAgent(agent_id="p1", responses=[_propose(summary="option B"), _pass()], family="b")
    engine = DebateEngine([p0, p1], max_rounds=2, enforce_heterogeneity=False)
    result = asyncio.run(engine.run(uuid4(), uuid4(), {}))

    assert len(result.candidates) == 2, "both distinct proposals must survive untouched"
    round2_amend_turn = [t for t in result.turns if t.speaker_id == "p0" and t.round_number == 2][0]
    assert round2_amend_turn.action == "pass", "ambiguous amend among 2+ candidates must not guess"
    assert all(len(c.supporters) == 1 for c in result.candidates), \
        "neither candidate should have gained a phantom second supporter"


def test_agent_failure_does_not_abort_the_round():
    class Exploding:
        agent_id, model_id, family = "bad", "m", "boom"

        async def respond(self, system, user):
            raise RuntimeError("rate limited")

    panel = [Exploding(), MockAgent(agent_id="ok", responses=[_propose(), _pass()], family="ok")]
    result = asyncio.run(DebateEngine(panel).run(uuid4(), uuid4(), {}))
    assert len(result.candidates) == 1  # the healthy agent still contributed
    assert all(t.speaker_id == "ok" for t in result.turns)


def test_state_machine_rejects_skipped_steps():
    assert can_transition("OPEN", "IN_DEBATE")
    assert not can_transition("OPEN", "APPROVED")
    assert not can_transition("APPROVED", "IN_DEBATE")  # terminal
    with pytest.raises(IllegalTransition, match="cannot move debate"):
        assert_transition("PENDING_EVAL", "APPROVED")
    # REJECTED is reachable from every pre-decision state
    for s in ("OPEN", "IN_DEBATE", "PENDING_EVAL", "PENDING_APPROVAL"):
        assert can_transition(s, "REJECTED")


def test_change_set_validation():
    empty = ChangeSet()
    assert any("empty" in p for p in empty.validate_ops())

    protected = ChangeSet(ops=[{
        "op_type": "update_task_node", "task_node_id": TASK_ID,
        "changes": {"tenant_id": "x"}, "reason": "sneaky",
    }])
    assert any("protected field" in p for p in protected.validate_ops())

    ok = ChangeSet(ops=[{
        "op_type": "update_task_node", "task_node_id": TASK_ID,
        "changes": {"description": "clearer"}, "reason": "clarity",
    }])
    assert ok.validate_ops() == []


def test_judge_must_be_independent():
    panel = _mock_panel([[], []])
    with pytest.raises(JudgeNotIndependent, match="also a panelist"):
        enforce_independence(panel[0], panel)
    same_family = MockAgent(agent_id="judge", responses=[], family="fam0")
    with pytest.raises(JudgeNotIndependent, match="family"):
        enforce_independence(same_family, panel)
    enforce_independence(MockAgent(agent_id="j", responses=[], family="other"), panel)


def test_layer1_fails_closed_when_judge_is_unavailable():
    """A broken judge must never yield a passing scorecard."""

    class DeadJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            raise RuntimeError("api down")

    cand = Candidate(
        debate_id=uuid4(), summary="s", rationale="r",
        change_set=ChangeSet(ops=[{
            "op_type": "update_task_node", "task_node_id": TASK_ID,
            "changes": {"description": "x"}, "reason": "y",
        }]),
    )
    result = asyncio.run(Layer1Evaluator(DeadJudge()).evaluate(cand))
    assert result.passed is False
    assert "judge unavailable" in result.notes


def test_layer1_discards_invented_fallacy_categories():
    class InventiveJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            return json.dumps({
                "fallacy_flags": [
                    {"fallacy": "vibes_based", "quote": "q", "explanation": "e"},
                    {"fallacy": "asiddha", "quote": "q2", "explanation": "e2"},
                ],
                "constructive": True, "notes": "",
            })

    cand = Candidate(debate_id=uuid4(), summary="s", rationale="r",
                     change_set=ChangeSet(ops=[{
                         "op_type": "update_task_node", "task_node_id": TASK_ID,
                         "changes": {"description": "x"}, "reason": "y"}]))
    result = asyncio.run(Layer1Evaluator(InventiveJudge()).evaluate(cand))
    assert [f.fallacy for f in result.fallacy_flags] == ["asiddha"]


def test_uncited_proposal_scores_zero_groundedness():
    class CleanJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            return json.dumps({"fallacy_flags": [], "constructive": True, "notes": ""})

    cand = Candidate(debate_id=uuid4(), summary="s", rationale="r",
                     change_set=ChangeSet(ops=[{
                         "op_type": "update_task_node", "task_node_id": TASK_ID,
                         "changes": {"description": "x"}, "reason": "y"}]))
    result = asyncio.run(Layer1Evaluator(CleanJudge()).evaluate(cand, cited=[]))
    assert result.groundedness_score == 0.0
    assert result.passed is False  # clean argument, but nothing anchors it


def test_no_action_justified_can_pass_with_empty_change_set():
    """
    Real bug this guards against: 3 real PEP debates where every
    panelist unanimously and correctly concluded no update was needed
    still failed Layer 1, because validate_ops() flags ANY empty ops
    list as a structural problem and passed requires zero structural
    problems -- a genuinely correct diagnosis was indistinguishable
    from a malformed, failed-to-produce-anything turn. An explicit
    no_action_justified=True must let this specific, deliberate
    conclusion pass (still gated by real citations and a clean judge --
    this isn't a bypass of everything, only of the empty-ops complaint).
    """
    class CleanJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            return json.dumps({"fallacy_flags": [], "constructive": True, "notes": ""})

    cand = Candidate(
        debate_id=uuid4(), summary="false positive, no action needed",
        rationale="both nodes agree on the supersession relationship already",
        change_set=ChangeSet(ops=[]),
        no_action_justified=True,
    )
    cited = [Citation(node_id=uuid4(), node_table="knowledge_nodes")]
    result = asyncio.run(Layer1Evaluator(CleanJudge()).evaluate(cand, cited=cited))
    # The actual point of this test: the empty-ops structural complaint
    # must be gone. groundedness_score/passed still depend on a real
    # graph to resolve citations against (unavailable offline, same
    # documented limitation as test_uncited_proposal_scores_zero_
    # groundedness above) -- not what this test is checking.
    assert result.structural_problems == []


def test_empty_change_set_without_no_action_justified_still_fails():
    """The original protection must survive: an empty ops list WITHOUT
    the explicit flag is still treated as a malformed/failed turn, not
    silently upgraded to a pass just because SOME candidates now can."""
    class CleanJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            return json.dumps({"fallacy_flags": [], "constructive": True, "notes": ""})

    cand = Candidate(
        debate_id=uuid4(), summary="s", rationale="r",
        change_set=ChangeSet(ops=[]),
        no_action_justified=False,
    )
    result = asyncio.run(Layer1Evaluator(CleanJudge()).evaluate(cand, cited=[]))
    assert "change set is empty -- candidate proposes no actual change" in result.structural_problems
    assert result.passed is False


def test_no_action_justified_actually_passes_end_to_end_with_a_real_graph():
    """Not just 'the structural complaint is gone' in isolation --
    confirms the full passed=True path works when citations resolve
    against a real graph, which is the actual claim that matters."""
    class CleanJudge:
        agent_id, model_id, family = "j", "m", "j"

        async def respond(self, system, user):
            return json.dumps({"fallacy_flags": [], "constructive": True, "notes": ""})

    class FakeGraph:
        async def node_exists(self, node_id, node_table):
            return True  # both cited nodes are real

    cite_id = uuid4()
    cand = Candidate(
        debate_id=uuid4(), summary="false positive, no action needed",
        rationale="both nodes agree on the supersession relationship already",
        change_set=ChangeSet(ops=[]),
        no_action_justified=True,
    )
    cited = [Citation(node_id=cite_id, node_table="knowledge_nodes")]
    result = asyncio.run(Layer1Evaluator(CleanJudge(), graph=FakeGraph()).evaluate(cand, cited=cited))
    assert result.groundedness_score == 1.0
    assert result.structural_problems == []
    assert result.passed is True
