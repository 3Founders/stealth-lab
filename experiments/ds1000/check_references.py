"""Eligibility, decided BEFORE any model runs: a problem is eligible when its library is
in scope and DS-1000's own reference solution passes the gold grading (and the screen)
in the pinned evaluation environment. Writes runs/eligibility.json.

    python check_references.py
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluate import grade

HERE = Path(__file__).resolve().parent
LIBRARIES = ("Pandas", "Numpy", "Scipy", "Sklearn")     # Matplotlib/Pytorch/Tensorflow out of scope (see README)


def rows() -> list[dict]:
    return [json.loads(line) for line in (HERE / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines()]


def main() -> None:
    scope = [r for r in rows() if r["metadata"]["library"] in LIBRARIES]

    def one(r):
        m = r["metadata"]
        g = grade(r["reference_code"], r["code_context"], m["test_case_cnt"])
        return str(m["problem_id"]), {"eligible": g["gold_pass"] and g["check_pass"], "check_kind": g["check_kind"],
                                      "gold": g["gold_pass"], "check": g["check_pass"], "error": g["error"]}

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = dict(ex.map(one, scope))
    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "runs" / "eligibility.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    ok = sum(v["eligible"] for v in out.values())
    print(f"in scope {len(scope)}, eligible {ok}")
    from collections import Counter
    print(Counter((r["metadata"]["library"], out[str(r["metadata"]["problem_id"])]["eligible"]) for r in scope))
    print(Counter(v["check_kind"] for v in out.values() if v["eligible"]))


if __name__ == "__main__":
    main()
