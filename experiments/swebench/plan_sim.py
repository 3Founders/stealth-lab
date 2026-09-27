"""Critical-path simulator for one benchmark run of the protocol, from MEASURED per-episode step counts.

    python plan_sim.py                       # grid over concurrency x seconds-per-step
    python plan_sim.py --conc 30 --step-s 4  # one scenario, stage by stage

Stages and dependencies (docs/knowledge_side_improvements.md, operator steps 4-9):
    calibration (40 + 60 step budgets, concurrently) -> freeze -> train (A0) -> [grading tail] -> learn -> notes
    -> test A0 -> test A0r + KP + K/E/C1/C2 (fresh only where notes exist) -> [grading tail] -> analyze
Grading streams alongside generation (gce_queue.py stream), so it only adds a TAIL after the last episode of a
stage whose grades the next stage needs (train -> learn; test -> analyze). Episode duration = steps x step
seconds, steps sampled (seeded) from the calibration attempts; episodes run on a fixed pool (list scheduling).
Model tokens: per-episode prompt/completion tokens from the same attempts, so the TPM each scenario needs is
reported (the endpoint must sustain it).
"""
from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics as st

import swe_env

SEED = 7


def calibration_profile() -> tuple[list[int], list[int], list[int]]:
    steps, tin, tout = [], [], []
    for p in sorted(swe_env.RUNS.glob("attempts_calibration_A0_s*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not r.get("environmental_failure") and r.get("steps"):
                    steps.append(int(r["steps"]))
                    tin.append(int((r.get("usage") or {}).get("prompt_tokens") or 0))
                    tout.append(int((r.get("usage") or {}).get("completion_tokens") or 0))
    if not steps:
        raise SystemExit("no clean calibration attempts to sample from")
    return steps, tin, tout


def makespan(n: int, conc: int, step_s: float, steps: list[int], rng: random.Random, cap: int | None = None) -> float:
    """List scheduling of n episodes on `conc` slots; returns wall seconds."""
    if n <= 0:
        return 0.0
    slots = [0.0] * min(conc, n)
    heapq.heapify(slots)
    for _ in range(n):
        s = rng.choice(steps)
        s = min(s, cap) if cap else s
        t = heapq.heappop(slots)
        heapq.heappush(slots, t + s * step_s)
    return max(slots)


def scenario(conc: int, step_s: float, train: int, test: int, notes_cov: float, learn_min: float,
             notes_min: float, grade_tail_min: float, calib_n: int = 12) -> dict:
    steps, tin, tout = calibration_profile()
    rng = random.Random(SEED)
    stages = {}
    stages["calibration"] = max(makespan(calib_n, conc // 2 or 1, step_s, steps, rng, cap=40),
                                makespan(calib_n, conc // 2 or 1, step_s, steps, rng, cap=60)) / 60
    stages["freeze"] = 2.0
    stages["train_gen"] = makespan(train, conc, step_s, steps, rng) / 60
    stages["train_grade_tail"] = grade_tail_min
    stages["learn"] = learn_min
    stages["notes"] = notes_min
    stages["test_A0"] = makespan(test, conc, step_s, steps, rng) / 60
    fresh = int(test * 2 + test * 4 * notes_cov)        # A0r + KP always fresh; K/E/C1/C2 only where notes exist
    stages["test_others"] = makespan(fresh, conc, step_s, steps, rng) / 60
    stages["test_grade_tail"] = grade_tail_min
    stages["analyze"] = 3.0
    total = sum(stages.values())
    episodes = 2 * calib_n + train + test + fresh
    tokens = episodes * st.mean(tin) + episodes * st.mean(tout)
    gen_min = stages["calibration"] + stages["train_gen"] + stages["test_A0"] + stages["test_others"]
    per_step_prompt = st.mean(t / s for t, s in zip(tin, steps))
    return {"stages_min": {k: round(v, 1) for k, v in stages.items()}, "total_h": round(total / 60, 2),
            "episodes": episodes, "tokens_M": round(tokens / 1e6), "avg_tpm_M": round(tokens / gen_min / 1e6, 2),
            "peak_tpm_M": round(conc * (per_step_prompt + st.mean(tout) / st.mean(steps)) / step_s * 60 / 1e6, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conc", type=int)
    ap.add_argument("--step-s", type=float)
    ap.add_argument("--train", type=int, default=317)
    ap.add_argument("--test", type=int, default=193)
    ap.add_argument("--notes-cov", type=float, default=1.0, help="share of test instances with K/E/C1/C2 notes")
    ap.add_argument("--learn-min", type=float, default=45.0)
    ap.add_argument("--notes-min", type=float, default=15.0)
    ap.add_argument("--grade-tail-min", type=float, default=6.0)
    a = ap.parse_args()
    kw = dict(train=a.train, test=a.test, notes_cov=a.notes_cov, learn_min=a.learn_min, notes_min=a.notes_min,
              grade_tail_min=a.grade_tail_min)
    if a.conc and a.step_s:
        print(json.dumps(scenario(a.conc, a.step_s, **kw), indent=1))
        return
    steps, _, _ = calibration_profile()
    print(f"calibration profile: {len(steps)} episodes, steps median {st.median(steps)}, max {max(steps)}")
    print(f"{'conc':>5} {'step_s':>6} {'total_h':>8} {'gen_eps':>8} {'tokens_M':>9} {'avg_TPM_M':>10} {'peak_TPM_M':>11}")
    for conc in (10, 20, 30, 50, 80):
        for step_s in (3, 5, 8, 12):
            r = scenario(conc, step_s, **kw)
            print(f"{conc:>5} {step_s:>6} {r['total_h']:>8} {r['episodes']:>8} {r['tokens_M']:>9} {r['avg_tpm_M']:>10} {r['peak_tpm_M']:>11}")


if __name__ == "__main__":
    main()
