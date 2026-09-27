"""Offline: arm KH (the Claude Code knowledge hook) -- prompt assembly with the shipped hook formatter, the round-5
flags in the environment, grading (never copied from A0) and the product verdict."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze  # noqa: E402
import grade  # noqa: E402
import kprod  # noqa: E402
import swe_env  # noqa: E402

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="the hook formatter runs in Node.js")


class FakeBridge:
    def __init__(self, reply: dict) -> None:
        self.reply, self.calls = reply, []

    def find_ways(self, query: str, repo_claims: str, session: str | None = None) -> str:
        self.calls.append({"query": query, "claims": repo_claims, "session": session})
        return json.dumps(self.reply)


def test_round5_flags_follow_kel_settings():
    ks = swe_env.CONFIG["kel_settings"]
    assert os.environ["KNOWLEDGE_RELATED_EXAMPLES"] == ("true" if ks.get("related_examples") else "false")
    assert os.environ["KNOWLEDGE_SUGGESTED_CANDIDATE"] == ("true" if ks.get("suggested_candidate") else "false")
    assert "KH" in swe_env.CONFIG["arms"]


@needs_node
def test_hook_prompt_appends_the_hooks_own_text_after_the_issue():
    reply = {"outcome": "ambiguous", "candidates": [],
             "suggested": {"goal_name": "Fix header parsing", "verified_solution": {"code": "x = 1", "language": "python"}},
             "related_examples": [{"goal_name": "Parse cookies", "verified_solution": {"code": "y = 2"}}]}
    bridge = FakeBridge(reply)
    message, text, summary = kprod.hook_prompt("Headers with spaces break the parser.", "CLAIMS", bridge, "wt#hook")
    assert bridge.calls == [{"query": "Headers with spaces break the parser.", "claims": "CLAIMS", "session": "wt#hook"}]
    assert message.startswith("Headers with spaces break the parser.\n\n" + text)
    assert message.endswith(kprod.KH_ADAPTATIONS)
    assert "Fix header parsing" in text and "Parse cookies" in text and "x = 1" in text
    assert summary == {"outcome": "ambiguous", "related_examples": 1, "suggested": True, "procedures": 0,
                       "context_chars": len(text)}
    assert "plan_and_run" not in message


@needs_node
def test_hook_that_finds_nothing_leaves_the_issue_alone():
    message, text, summary = kprod.hook_prompt("Issue.", None, FakeBridge({"outcome": "no_match"}), "s")
    assert text == "" and summary["context_chars"] == 0
    assert message == "Issue.\n\n" + kprod.KH_ADAPTATIONS


def test_kh_grades_are_never_copied_from_a0(tmp_path, monkeypatch):
    monkeypatch.setattr(swe_env, "RUNS", tmp_path)
    (tmp_path / "predictions_test_A0.jsonl").write_text(json.dumps({"instance_id": "a", "model_patch": "P"}) + "\n")
    (tmp_path / "grades_test_A0.json").write_text(json.dumps({"a": {"resolved": True, "status": "resolved"}}))
    (tmp_path / "attempts_test_KH.jsonl").write_text(json.dumps({"instance_id": "a", "reused_from": "A0"}) + "\n")
    assert grade.reused_a0_grades("test_KH", {"a": "P"}) == {}


def test_product_decision_for_kh(tmp_path, monkeypatch):
    monkeypatch.setattr(swe_env, "RUNS", tmp_path)
    ids = [f"r/x-{i}" for i in range(40)]
    repo_of = {i: "r/x" for i in ids}
    res = {"A0": {i: n < 10 for n, i in enumerate(ids)}, "A0r": {i: n < 10 for n, i in enumerate(ids)},
           "KH": {i: n < 25 for n, i in enumerate(ids)}}
    rows = [{"instance_id": i, "kel_calls": [], "hook": {"outcome": "resolved", "related_examples": 2, "suggested": False,
                                                         "procedures": 1, "context_chars": 900}} for i in ids]
    (tmp_path / "attempts_test_KH.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    rep = {"secondary": {"KH-A0": analyze.paired(ids, res["A0"], res["KH"], repo_of),
                         "KH-A0r": analyze.paired(ids, res["A0r"], res["KH"], repo_of)}}
    cost = {"A0": {"tokens_per_resolved": 1000}, "KH": {"tokens_per_resolved": 1100}}
    usage, decision = analyze.product_decision("KH", ids, res, cost, rep, repo_of, {})
    assert decision["VERDICT"] == "HOOK HELPS" and decision["1_KH_beats_A0"] and decision["4_cost_ok"]
    assert usage["hook"]["episodes"] == 40 and usage["hook"]["with_related_examples"] == 40
    cost["KH"]["tokens_per_resolved"] = 1300           # > 1.2x A0 per resolved: rule 4 fails
    assert analyze.product_decision("KH", ids, res, cost, rep, repo_of, {})[1]["VERDICT"] == "NOT SHOWN"
