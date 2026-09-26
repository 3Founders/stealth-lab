"""Re-grade attempts that timed out, with a longer limit (a correct-but-slow solution on
this 4-CPU machine must not count as a failure). Rewrites runs/attempts.jsonl in place.

    python regrade.py --timeout 240
"""
from __future__ import annotations

import argparse
import json

import demo_env  # noqa: F401

from evaluate import run_tests
from run_models import ATTEMPTS, sample_tasks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args()
    rows = [json.loads(line) for line in ATTEMPTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    tasks = {**sample_tasks("fit"), **sample_tasks("heldout")}
    changed = 0
    for r in rows:
        if (r.get("eval_error") or "").startswith("timeout"):
            t = tasks[r["task_id"]]
            res = run_tests(r["code"], t.test_code, t.visible_tests, timeout_s=a.timeout)
            print(r["task_id"], r["model"], r["condition"], "->", res["gold_pass"], res.get("seconds"),
                  (res.get("error") or "")[:60], flush=True)
            r.update(visible_pass=res["visible_pass"], gold_pass=res["gold_pass"], tests=res.get("tests", {}),
                     failures=res.get("failures", {}), eval_error=res.get("error"), regraded_timeout_s=a.timeout)
            changed += 1
    ATTEMPTS.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print("regraded", changed)


if __name__ == "__main__":
    main()
