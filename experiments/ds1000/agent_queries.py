"""The request an AGENT would send to find_ways: a short description in its own words, not the
pasted problem. One fixed writer (gemma-4-31B-it, temperature 0) for every test problem, so the
query is identical across arms; written from the problem text only (no tests, no references).

    python agent_queries.py      # writes <runs>/agent_queries.json
"""
from __future__ import annotations

import json
import re

import demo_env

from common import problems, test_items
from models import GeneralCompute

WRITER = "gemma-4-31B-it"
SYSTEM = ("You are a coding agent about to solve a user's task. Before writing code you call a knowledge tool, "
          "find_ways, with a short request describing what you need to accomplish, so it can return known ways "
          "to do it. Write that request: one or two sentences, at most 40 words, stating the goal (what to "
          "compute or transform, and with which library). No variable names, no concrete data values. "
          "Reply with the request only.")


def main() -> None:
    out_path = demo_env.RUNS / "agent_queries.json"
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    gc = GeneralCompute()
    for t in test_items():
        pid = t["problem_id"]
        if pid in out:
            continue
        question = problems()[pid]["prompt"].split("\nA:\n")[0].strip()[:3000]
        r = gc.complete(WRITER, question, system=SYSTEM, max_tokens=120)
        text = re.sub(r"\s+", " ", (r.text or "").strip().strip('"'))
        if r.error or not text:
            print(f"{pid}: failed {r.error}")
            continue
        out[pid] = text
        out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"{pid:>4} {text}")
    print(f"queries: {len(out)} of {len(test_items())}")


if __name__ == "__main__":
    main()
