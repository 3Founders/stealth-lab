"""Choose agent.max_steps BEFORE any scored run (the previous agent experiment failed on exactly this:
every trial exhausted its step budget).

    python generate.py --part calibration --arm A0 --max-steps 40
    python generate.py --part calibration --arm A0 --max-steps 60
    python calibrate.py

Rule (fixed here, before seeing any scored data): choose the SMALLEST candidate at which at least
80% of calibration episodes end with stop_reason "finished" (not "step_budget"); if none reaches 80%, choose the
largest candidate and record that as a deviation. Then write the chosen value into
experiment.json agent.max_steps, commit, and never change it.
"""
from __future__ import annotations

import json

import swe_env
from generate import load_jsonl

THRESHOLD = 0.8


def main() -> None:
    rows = []
    for steps in swe_env.CONFIG["agent"]["max_steps_candidates"]:
        recs = [r for r in load_jsonl(swe_env.RUNS / f"attempts_calibration_A0_s{steps}.jsonl")
                if not r.get("environmental_failure")]
        if not recs:
            print(f"max_steps={steps}: not run yet")
            continue
        finished = sum(1 for r in recs if r.get("stop_reason") == "finished") / len(recs)
        rows.append((steps, finished, len(recs)))
        print(f"max_steps={steps}: {finished:.0%} finished ({len(recs)} episodes), "
              f"median steps {sorted(r['steps'] for r in recs)[len(recs) // 2]}")
    ok = [s for s, f, _ in rows if f >= THRESHOLD]
    if rows:
        choice = min(ok) if ok else max(s for s, _, _ in rows)
        print(f"\nCHOSEN max_steps = {choice}" + ("" if ok else "  (no candidate reached 80% -- log as a deviation)"))
        print("Write it into experiment.json -> agent.max_steps, commit, then continue with the train pool.")


if __name__ == "__main__":
    main()
