"""
Runs the Task B 2x2 (run_task_b_2x2.py's run_2x2_once) N times, and
aggregates real statistics per cell: pass rate, mean/stdev of total
tokens AND completion-tokens-only (the latter strips out the hint's
fixed transmission cost, isolating the model's actual work -- a real
confound found in the first single-run result: raw total tokens made
the hinted cells look worse partly just because the hint itself costs
prompt tokens to send every turn, regardless of whether it helped).

Run from backend/:
    python scripts/synthetic_tasks/run_task_b_repeated.py --n 5
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

from run_task_b_2x2 import run_2x2_once

HERE = Path(__file__).parent


def summarize(label: str, runs: list[dict]) -> dict:
    passed = [r["passed"] for r in runs]
    total_tokens = [r["total_tokens"] for r in runs]
    completion_tokens = [r["total_completion_tokens"] for r in runs]
    turns = [r["turns_used"] for r in runs]
    return {
        "label": label,
        "n": len(runs),
        "pass_rate": sum(passed) / len(passed),
        "passes": sum(passed),
        "total_tokens_mean": statistics.mean(total_tokens),
        "total_tokens_stdev": statistics.stdev(total_tokens) if len(total_tokens) > 1 else 0.0,
        "completion_tokens_mean": statistics.mean(completion_tokens),
        "completion_tokens_stdev": statistics.stdev(completion_tokens) if len(completion_tokens) > 1 else 0.0,
        "turns_mean": statistics.mean(turns),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5, help="number of repetitions of the full 2x2")
    args = parser.parse_args()

    print(f"Running the Task B 2x2 {args.n} time(s)...")
    all_runs: dict[str, list[dict]] = {}  # label -> list of per-repetition results

    for i in range(args.n):
        print(f"\n{'#' * 70}\n# REPETITION {i + 1}/{args.n}\n{'#' * 70}")
        results = await run_2x2_once(run_id=f"_rep{i}")
        for r in results:
            all_runs.setdefault(r["label"], []).append(r)

    print(f"\n\n{'=' * 70}\nAGGREGATED SUMMARY over {args.n} repetition(s)\n{'=' * 70}")
    summaries = []
    header = f"{'Cell':<45} {'PassRate':<10} {'TotalTok(mean±sd)':<22} {'CompTok(mean±sd)':<22} {'Turns':<7}"
    print(header)
    for label, runs in all_runs.items():
        s = summarize(label, runs)
        summaries.append(s)
        print(f"{label:<45} {s['passes']}/{s['n']:<8} "
              f"{s['total_tokens_mean']:.0f}±{s['total_tokens_stdev']:.0f}{'':<10} "
              f"{s['completion_tokens_mean']:.0f}±{s['completion_tokens_stdev']:.0f}{'':<10} "
              f"{s['turns_mean']:.1f}")

    out = {"n_repetitions": args.n, "per_repetition": all_runs, "summary": summaries}
    (HERE / "task_b_repeated_results.json").write_text(json.dumps(out, indent=2))
    print(f"\nfull results saved to {HERE / 'task_b_repeated_results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
