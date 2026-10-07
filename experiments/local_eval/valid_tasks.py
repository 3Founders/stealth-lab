"""Fix the scored task set from the validity checks, BEFORE any arm is graded (PREREGISTRATION.md section 6).

    .venv/Scripts/python valid_tasks.py [--part test]     # -> runs/valid_tasks.json

A task is valid when its tests can tell a fix from no fix in our runner:
  * the gold patch resolves it (runs/tests_gold_<part>.json), and
  * an empty patch does not (runs/tests_empty_<part>.json).
A task whose gold or empty run still errors after retries is invalid too (the environment is broken). The rule
uses no arm's output, so it cannot favour any arm. Right-cause grading does not depend on the tests, so it is
reported on BOTH the valid set (primary) and the full design (sensitivity).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from common import RUNS, read_json, write_json


def classify(gold: dict | None, empty: dict | None) -> str:
    if gold is None or empty is None:
        return "not_checked"
    if gold["status"] == "error" or empty["status"] == "error":
        return "environment_error"
    if not gold["resolved"]:
        return "gold_fails"
    if empty["resolved"]:
        return "empty_passes"
    return "valid"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="test")
    a = ap.parse_args()
    ids = read_json(RUNS / "design.json")[a.part]
    gold = read_json(RUNS / f"tests_gold_{a.part}.json")
    empty = read_json(RUNS / f"tests_empty_{a.part}.json")
    verdict = {i: classify(gold.get(i), empty.get(i)) for i in ids}
    if any(v == "not_checked" for v in verdict.values()):
        raise SystemExit("run grade_tests.py --gold and --empty on every task first")
    out = {"part": a.part, "valid": [i for i in ids if verdict[i] == "valid"], "verdict": verdict,
           "counts": dict(Counter(verdict.values()))}
    write_json(RUNS / "valid_tasks.json", out)
    print(json.dumps(out["counts"], indent=1))


if __name__ == "__main__":
    main()
