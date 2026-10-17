"""Offline tests: pairing two survey runs per issue (stage 4 and acceptance_eval) and the pre-registered rule choice."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_eval as E  # noqa: E402
import pipeline_survey as P  # noqa: E402

SRC = "diff --git a/pkg/calc.py b/pkg/calc.py\n--- a/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def answer(patch=SRC, cost=0.05):
    return {"state": "verified", "files": ["pkg/calc.py"], "patch": patch, "cost_usd": cost, "tokens": {"in": 10}}


def test_merge_answers_names_each_runs_agent_and_keeps_the_rest():
    m = P.merge_answers({"r1": {"instance_id": "i", "repo": "o/r", "agents": {"claude": answer()}},
                         "r2": {"instance_id": "i", "repo": "o/r", "agents": {"claude": answer(cost=0.1)}}})
    assert set(m["agents"]) == {"claude@r1", "claude@r2"} and m["repo"] == "o/r"
    assert m["agents"]["claude@r2"]["cost_usd"] == 0.1


def write(tmp, name, rows):
    p = tmp / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def test_pair_rows_matches_stage4s_names_and_A_compares_the_two_runs(tmp_path):
    a = write(tmp_path, "run-a-stage3.jsonl", [{"instance_id": "i1", "agents": {"claude": answer()}},
                                               {"instance_id": "i2", "agents": {"claude": answer()}}])
    b = write(tmp_path, "run-b-stage3.jsonl", [{"instance_id": "i1", "agents": {"claude": answer()}},
                                               {"instance_id": "i2", "agents": {"claude": answer(SRC.replace("calc", "other"))}}])
    rows3 = E.pair_rows([a, b])
    assert {k for r in rows3 for k in r["agents"]} == {"claude@run-a", "claude@run-b"}
    rows4 = [{"instance_id": i, "gold_solved": True, "cross": {},
              "agents": {"claude@run-a": {"truly_solved": True, "existing_new_failures": 0, "hidden_tail": ""},
                         "claude@run-b": {"truly_solved": False, "existing_new_failures": 0, "hidden_tail": ""}}} for i in ("i1", "i2")]
    sig = [s for s in E.signals(rows3, rows4, None) if s["agent"] == "claude@run-a"]
    assert [s["A"] for s in sorted(sig, key=lambda s: s["iid"])] == [True, False]
    assert all(s["C_known"] and not s["C"] for s in sig), "cross-tested, no answer judged: C is known and false"


def sig(solved, **kw):
    base = {"uncertain": False, "solved": solved, "cost": 0.1, "E": True, "S": True, "F": True, "X": True, "A": False, "C": False,
            "T": False, "Tstar": True, "R": False, "Rno": False, "Rs": False, "Rs_no": False, "Ro": False, "Ro_no": False}
    return {**base, **kw}


def test_choose_takes_the_largest_saving_within_10_percent_of_all_tasks_else_rule_15():
    # 20 tasks: Ro says fixes on 10 right and 1 wrong answer (1 wrong <= 2 allowed); E S alone delivers 10 wrong
    s = [sig(True, Ro=True) for _ in range(10)] + [sig(False, Ro=True)] + [sig(False) for _ in range(9)]
    pick, lines = E.choose(s, 20, have_t=False, missing={})
    assert pick == "12", lines
    assert any(l.startswith('final run: --accept-rule "E S Rs" --review-model claude-opus-5-5') for l in lines)
    # every rule accepts the 20 wrong answers: none qualifies
    pick, lines = E.choose([sig(False, A=True, C=True, R=True, Rs=True, Ro=True) for _ in range(20)], 20, have_t=False, missing={})
    assert pick == "15" and "none qualifies" in lines[-2], lines


def test_choose_decides_nothing_while_a_signal_is_missing():
    pick, lines = E.choose([sig(True, Ro=True)], 1, have_t=False, missing={"Ro": 1})
    assert pick is None and lines[-1].startswith("NOT DECIDED")
    assert any("rule 12: not scored (Ro missing for 1)" in l for l in lines)
    assert any("rule 1: not scorable" in l for l in lines)


def test_series_answers_become_stage3_parts_one_agent_per_try(tmp_path):
    import series_to_parts as SP
    rows = [{"task_id": "i1", "arm": "C", "attempts": [
        {"model": "glm-5.3-flash", "state": "verified", "role": "attempt", "patch": SRC},
        {"model": "gemma", "state": "failed", "role": "attempt", "patch": None},
        {"model": "glm-5.3-flash", "state": "failed", "role": "attempt", "patch": SRC}]},
            {"task_id": "i1", "arm": "C0", "attempts": [{"model": "minimax", "state": "timed_out", "role": "attempt", "patch": SRC}]}]
    f = write(tmp_path, "r.jsonl", rows)
    got = SP.parts([f])
    assert set(got["i1"]["agents"]) == {"C.glm-5.3-flash.1", "C.glm-5.3-flash.3", "C0.minimax.1"}, "no patch, no answer"
    assert SP.main([str(tmp_path / "out"), f]) == 0 and (tmp_path / "out" / "i1.json").exists()
