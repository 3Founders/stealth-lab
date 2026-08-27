"""Offline proofs for semantic_judge (LLM-judge adjudication for Jaccard-
failing semantic_label pairs). Zero network: the client is a fake."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import error_floor  # noqa: E402
import semantic_judge as sj  # noqa: E402


class FakeClient:
    """Replies with queued contents, one per call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, *, task_id="", arm=""):
        self.calls.append({"task_id": task_id, "arm": arm,
                           "n_messages": len(messages)})
        content = self.replies.pop(0) if self.replies else "{}"
        return {"content": content, "model": "fake/judge",
                "tokens_in": 8, "tokens_out": 3}


class TestParseVerdict:
    def test_json_true_and_false(self):
        assert sj.parse_verdict('{"match": true}') is True
        assert sj.parse_verdict('{"match": false}') is False

    def test_code_fence_and_surrounding_prose(self):
        assert sj.parse_verdict('```json\n{"match": true}\n```') is True
        assert sj.parse_verdict('Sure, here it is:\n{"match": false} done.') is False

    def test_bare_yes_no_fallback(self):
        assert sj.parse_verdict("Yes, these match.") is True
        assert sj.parse_verdict("No, different changes.") is False

    def test_unparseable_returns_none(self):
        assert sj.parse_verdict("I cannot determine this.") is None
        assert sj.parse_verdict("") is None
        assert sj.parse_verdict("{not json") is None


class TestBuildMessages:
    def test_carries_both_labels_and_a_closed_schema(self):
        msgs = sj.build_messages("gold text", "pred text")
        assert msgs[0]["role"] == "system"
        assert "match" in msgs[0]["content"]
        assert "gold text" in msgs[1]["content"]
        assert "pred text" in msgs[1]["content"]


class TestSemanticJudge:
    def test_adjudicate_true(self):
        judge = sj.SemanticJudge(FakeClient(['{"match": true}']))
        assert judge.adjudicate("continuous integration pipeline configuration added",
                                "CI workflow configuration updated") is True
        assert judge.calls == 1
        assert judge.unparseable == 0

    def test_adjudicate_false(self):
        judge = sj.SemanticJudge(FakeClient(['{"match": false}']))
        assert judge.adjudicate("tests were refactored",
                                "build output directory was deleted") is False

    def test_unparseable_defaults_to_no_match(self):
        judge = sj.SemanticJudge(FakeClient(["I am not sure about this one."]))
        assert judge.adjudicate("a", "b") is False
        assert judge.unparseable == 1

    def test_arm_tag_is_judge(self):
        client = FakeClient(['{"match": true}'])
        judge = sj.SemanticJudge(client)
        judge.adjudicate("a", "b", excerpt_id="ef-sem-003")
        assert client.calls[0]["arm"] == "JUDGE"
        assert client.calls[0]["task_id"] == "ef-sem-003"


class TestErrorFloorIntegration:
    """Proves the judge composes with error_floor.py's TP/FP/FN accounting
    exactly as the brief's adoption shape describes: Jaccard first, judge
    only on a miss, judge=None stays byte-identical to prior grading."""

    def _excerpt(self, gold_label):
        return {"excerpt_id": "ef-test-judge",
                "trace_event": {"event_id": "ev-t", "tool_name": "Bash"},
                "gold": [{"observation_type": "semantic_label",
                         "label": gold_label}],
                "notes": "test-local excerpt"}

    def test_judge_converts_jaccard_miss_into_tp(self):
        gold = "continuous integration pipeline configuration added"
        pred = "CI workflow configuration updated"
        assert not error_floor.semantic_match(gold, pred)  # Jaccard alone fails

        judge = sj.SemanticJudge(FakeClient(['{"match": true}']))
        pred_obs = [{"observation_type": "semantic_label", "label": pred}]
        g = error_floor.grade_excerpt(self._excerpt(gold), pred_obs,
                                      judge=judge.as_error_floor_judge())
        assert (g["tp"], g["fp"], g["fn"]) == (1, 0, 0)
        assert judge.calls == 1

    def test_judge_none_default_is_unchanged(self):
        gold = "continuous integration pipeline configuration added"
        pred = "CI workflow configuration updated"
        pred_obs = [{"observation_type": "semantic_label", "label": pred}]
        g = error_floor.grade_excerpt(self._excerpt(gold), pred_obs)
        assert (g["tp"], g["fp"], g["fn"]) == (0, 1, 1)

    def test_judge_not_consulted_when_jaccard_already_matches(self):
        gold = "authentication implementation was modified"
        pred = "the authentication implementation was modified today"
        judge = sj.SemanticJudge(FakeClient([]))  # would raise IndexError if called
        pred_obs = [{"observation_type": "semantic_label", "label": pred}]
        g = error_floor.grade_excerpt(self._excerpt(gold), pred_obs,
                                      judge=judge.as_error_floor_judge())
        assert (g["tp"], g["fp"], g["fn"]) == (1, 0, 0)
        assert judge.calls == 0

    def test_judge_upholds_a_genuine_mismatch(self):
        gold = "authentication implementation was modified"
        pred = "build output directory was deleted"
        judge = sj.SemanticJudge(FakeClient(['{"match": false}']))
        pred_obs = [{"observation_type": "semantic_label", "label": pred}]
        g = error_floor.grade_excerpt(self._excerpt(gold), pred_obs,
                                      judge=judge.as_error_floor_judge())
        assert (g["tp"], g["fp"], g["fn"]) == (0, 1, 1)
        assert judge.calls == 1
