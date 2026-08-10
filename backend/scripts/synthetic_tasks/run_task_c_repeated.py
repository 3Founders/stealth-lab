"""
Runs Task C's full-system pipeline (real retrieval + real SLM
execution) N times, to check whether the clean first result holds up
or was itself just one lucky draw -- directly motivated by already
having observed a real 10x cost swing on an identical setup earlier
today (Cell 3, edit-pdf). One passing run proves the mechanism CAN
work; it doesn't establish that it reliably does.

Run from backend/:
    python scripts/synthetic_tasks/run_task_c_repeated.py --n 2
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from full_system_common import run_full_system_core
from retrieve_trajectory import EXPERIMENTAL_THRESHOLD_OVERRIDE
import task_c.generate as gen_c

HERE = Path(__file__).parent


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=2)
    args = parser.parse_args()

    runs = []
    for i in range(args.n):
        print(f"\n{'#' * 70}\n# TASK C REPETITION {i + 1}/{args.n}\n{'#' * 70}")
        out = await run_full_system_core(
            "Task C", "task_c", "shipments.csv", gen_c, HERE,
            retrieval_threshold=EXPERIMENTAL_THRESHOLD_OVERRIDE, run_suffix=f"_rep{i}",
        )
        runs.append(out)

    passed = [r["passed"] for r in runs]
    tokens = [r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in runs if r["passed"]]
    costs = [r.get("total_cost_usd", 0) for r in runs if r["passed"]]

    print(f"\n{'=' * 70}\nSUMMARY over {args.n} repetition(s)\n{'=' * 70}")
    print(f"Pass rate: {sum(passed)}/{len(passed)}")
    if tokens:
        print(f"Tokens (passed runs only): {tokens}, mean={statistics.mean(tokens):.0f}"
              + (f", stdev={statistics.stdev(tokens):.0f}" if len(tokens) > 1 else ""))
        print(f"Cost (passed runs only): {[f'${c:.6f}' for c in costs]}, mean=${statistics.mean(costs):.6f}"
              + (f", stdev=${statistics.stdev(costs):.6f}" if len(costs) > 1 else ""))

    (HERE / "task_c_repeated_results.json").write_text(json.dumps(runs, indent=2))
    print(f"\nfull results saved to {HERE / 'task_c_repeated_results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
