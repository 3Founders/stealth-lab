"""Offline tests of the output check (output_check.py): no model (the judge is a stand-in), no network.
Run from experiments/abtest:  python -m pytest test_output_check_offline.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import output_check as oc  # noqa: E402

REPO = HERE.parents[1]

# the message exactly as lib/dispatch.mjs builds it (a test below guards that the template has not drifted)
MSG = ("Done by gpt-oss-120b in 41 s (StealthLab dispatched it before this session's model ran, $0.0001). "
       "It passed python -m pytest -q in an isolated worktree and the change is applied: calc.py. "
       "Recorded in the library as L-1a2b. To put it back as it was: stealthlab-mcp undo."
       "\n\nThe add function now returns a + b instead of a - b.")
DIFF = ("diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n")
DIFF2 = DIFF + "diff --git a/tests/test_calc.py b/tests/test_calc.py\n--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1 +1 @@\n-x\n+y\n"


def judge(correct=True, honest=True, cost=0.02):
    calls = []

    def j(message, diff):
        calls.append((message, diff))
        return {"correct": correct, "honest": honest, "reason": "r", "cost_usd": cost}
    j.calls = calls
    return j


def item(message=MSG, diff=DIFF, id="t.C.t1", truncated=False):
    return {"id": id, "message": message, "diff": diff, "truncated": truncated}


# ---------------------------------------------------------------- the rubric matches the product's message

def test_the_message_template_in_dispatch_has_not_drifted_from_what_the_rubric_reads():
    src = (REPO / "packaging" / "npm" / "lib" / "dispatch.mjs").read_text(encoding="utf-8")
    for fragment in ("Done by ${rung.model} in ${secs} s (StealthLab dispatched it before this session's model ran${cost})",
                     "It passed ${checkLabel} in an isolated worktree and the change is applied: ",
                     "To put it back as it was: stealthlab-mcp undo."):
        assert fragment in src, fragment


# ---------------------------------------------------------------- the four criteria that need no model

def test_a_complete_message_scores_every_rule_criterion():
    pts = oc.rule_points(MSG, DIFF)
    assert {c: v[0] for c, v in pts.items()} == {"names": 1, "files": 1, "checked": 1, "undo": 1}
    assert "gpt-oss-120b" in pts["names"][1] and "pytest" in pts["checked"][1]


@pytest.mark.parametrize("message, criterion, why", [
    (MSG.replace(", $0.0001", ""), "names", "the cost"),
    (MSG.replace("Done by gpt-oss-120b in 41 s", "Done"), "names", "the model"),
    (MSG.replace(" To put it back as it was: stealthlab-mcp undo.", ""), "undo", "undo"),
    (MSG.replace("It passed python -m pytest -q in an isolated worktree and ", ""), "checked", "how it was checked"),
    (MSG.replace("applied: calc.py.", "applied: no files."), "files", "missing from the message: calc.py"),
])
def test_each_rule_criterion_fails_on_its_own(message, criterion, why):
    pts = oc.rule_points(message, DIFF)
    assert pts[criterion][0] == 0 and why in pts[criterion][1]
    assert sum(v[0] for v in pts.values()) == 3, "only that criterion fails"


def test_files_are_compared_with_the_diff_and_the_arms_own_directories_do_not_count():
    assert oc.rule_points(MSG, DIFF2)["files"] == (0, "missing from the message: tests/test_calc.py")
    both = MSG.replace("applied: calc.py.", "applied: calc.py, tests/test_calc.py.")
    assert oc.rule_points(both, DIFF2)["files"][0] == 1
    own = DIFF + "diff --git a/.stealth/library.md b/.stealth/library.md\n--- a/.stealth/library.md\n"
    assert oc.diff_files(own) == ["calc.py"] and oc.rule_points(MSG, own)["files"][0] == 1
    assert oc.rule_points(MSG, "")["files"][0] == 0, "an empty diff but files named: a claim the diff does not support"
    assert oc.rule_points(MSG.replace("applied: calc.py.", "applied: no files."), "")["files"][0] == 1
    assert oc.rule_points(MSG, None)["files"][0] == 1 and oc.rule_points(MSG.replace("calc.py", "no files"), None)["files"][0] == 0


# ---------------------------------------------------------------- scoring with the judge

def test_scored_out_of_six_with_the_judge_and_the_judge_sees_message_and_diff():
    j = judge()
    s = oc.score_item(item(), j)
    assert s["total"] == 6 and s["of"] == 6 and s["not_scored"] == [] and s["judge_cost_usd"] == 0.02
    assert j.calls == [(MSG, DIFF)]
    s = oc.score_item(item(), judge(correct=True, honest=False))
    assert s["total"] == 5 and s["points"]["honest"] == 0 and s["points"]["correct"] == 1
    assert oc.score_item(item(), judge(False, False))["total"] == 4


def test_without_a_diff_or_a_judge_two_criteria_are_not_scored():
    j = judge()
    s = oc.score_item(item(diff=None), j)
    assert s["of"] == 4 and s["total"] == 4 and set(s["not_scored"]) == {"correct", "honest"} and j.calls == []
    s = oc.score_item(item(), None)
    assert s["of"] == 4 and s["not_scored"] == ["correct", "honest"] and s["judge_cost_usd"] == 0


def test_a_judge_that_fails_costs_that_message_two_criteria_not_the_run():
    def broken(message, diff):
        raise RuntimeError("the judge returned no JSON (exit 1)")
    s = oc.score_item(item(), broken)
    assert s["of"] == 4 and s["not_scored"] == ["correct", "honest"] and "no JSON" in s["why"]["judge_error"]


def test_the_report_gives_the_mean_and_every_message_below_five():
    scores = [oc.score_item(item(id="a"), judge()),
              oc.score_item(item(MSG.replace(" To put it back as it was: stealthlab-mcp undo.", ""), id="b"), judge()),
              oc.score_item(item(MSG.replace(", $0.0001", "").replace(" To put it back as it was: stealthlab-mcp undo.", ""), id="c"), judge(honest=False)),
              oc.score_item(item(diff=None, id="d", truncated=True), None)]
    text = oc.report(scores)
    assert "4 message(s)" in text and "scored out of 6: 3 message(s), mean 4.67/6" in text and "scored out of 4: 1 message(s), mean 4.00/4" in text
    assert "below 5/6 (below max-1 where scored out of fewer): 1" in text
    assert "  c  3/6  names: does not name the cost; undo: does not say how to undo; honest: r" in text
    assert "  b " not in text and "  a " not in text, "5/6 and 6/6 are not listed"
    assert "1 message(s) were cut at 400 characters" in text and "per criterion: names" in text
    assert oc.report([]) == "no dispatched message found in the input"
    assert [oc.threshold(n) for n in (6, 4, 2, 1)] == [5, 3, 1, 1]


# ---------------------------------------------------------------- reading input

def test_messages_come_from_three_kinds_of_input_line():
    assert oc.items_from_line({"id": "x", "message": "Done by m in 1 s", "diff": "d"}) == [
        {"id": "x", "message": "Done by m in 1 s", "diff": "d", "truncated": False}]
    turns = {"task_id": "t1", "arm": "C", "turn_log": [{"turn": 1, "shown": None}, {"turn": 2, "shown": MSG, "diff": DIFF},
                                                       {"turn": 3, "shown": "not dispatched text"}], "final": MSG}
    assert oc.items_from_line(turns) == [{"id": "t1.C.t2", "message": MSG, "diff": DIFF, "truncated": False}]
    one = oc.items_from_line({"task_id": "t2", "arm": "C0", "final": MSG})
    assert one == [{"id": "t2.C0", "message": MSG, "diff": None, "truncated": False}]
    assert oc.items_from_line({"task_id": "t3", "arm": "C", "final": "Done by x in 3 s " + "y" * 400})[0]["truncated"] is True
    assert oc.items_from_line({"task_id": "t4", "arm": "A", "final": "I changed calc.py."}) == []


def test_the_message_is_found_in_a_stream_json_log(tmp_path):
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(json.dumps(x) for x in [
        {"type": "system", "subtype": "init"},
        {"type": "system", "subtype": "hook_response", "stdout": json.dumps({"decision": "block", "reason": MSG})},
        {"type": "result", "result": ""}]) + "\nnot json\n", encoding="utf-8")
    assert oc.message_from_log(log) == MSG
    plain = tmp_path / "p.jsonl"
    plain.write_text(json.dumps({"type": "result", "result": "I changed calc.py."}) + "\n", encoding="utf-8")
    assert oc.message_from_log(plain) is None and oc.message_from_log(tmp_path / "missing.jsonl") is None


# ---------------------------------------------------------------- the judge's command

class Done:
    def __init__(self, stdout, code=0):
        self.stdout, self.returncode = stdout, code


def test_claude_judge_runs_sonnet_headless_in_an_empty_directory_and_reads_the_verdict(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, kw=kw)
        return Done(json.dumps({"result": 'Sure.\n{"correct": true, "honest": false, "reason": "claims a test"}', "total_cost_usd": 0.015}))

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    monkeypatch.setattr(oc.shutil, "which", lambda name: "claude")
    v = oc.claude_judge()(MSG, DIFF)
    assert v == {"correct": True, "honest": False, "reason": "claims a test", "cost_usd": 0.015}
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == oc.JUDGE_MODEL
    assert cmd[cmd.index("--system-prompt") + 1] == oc.JUDGE_SYSTEM and cmd[cmd.index("--setting-sources") + 1] == "project"
    assert "MESSAGE:" in seen["kw"]["input"] and "return a + b" in seen["kw"]["input"], "the prompt goes in on stdin, with message and diff"
    assert Path(seen["kw"]["cwd"]).name != "abtest", "an empty temp directory, not the repository"


def test_claude_judge_errors_are_clear(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(oc.subprocess, "run", lambda cmd, **kw: Done("garbage", 1))
    with pytest.raises(RuntimeError, match="no JSON"):
        oc.claude_judge()(MSG, DIFF)
    monkeypatch.setattr(oc.subprocess, "run", lambda cmd, **kw: Done(json.dumps({"result": "no verdict here"})))
    with pytest.raises(RuntimeError, match="not the verdict"):
        oc.claude_judge()(MSG, DIFF)
    assert oc.parse_verdict('{"correct": "yes", "honest": true}') is None


# ---------------------------------------------------------------- the command line

def test_cli_scores_a_file_without_a_model_and_writes_the_scores(tmp_path, capsys):
    f = tmp_path / "msgs.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [{"id": "ok", "message": MSG, "diff": DIFF},
                                                    {"id": "bad", "message": "Done by m in 2 s. changed stuff"}]) + "\n", encoding="utf-8")
    out = tmp_path / "scores.jsonl"
    assert oc.main([str(f), "--judge", "none", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "2 message(s)" in text and "scored out of 4: 2 message(s)" in text and "  bad " in text and "  ok " not in text
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["ok", "bad"] and rows[0]["total"] == 4
    assert oc.main([]) == 2 and oc.main([str(f), "--judge", "gpt"]) == 2
