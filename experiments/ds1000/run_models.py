"""Run the open models for one arm; grade locally; append to runs/attempts.jsonl.

    python run_models.py fit_raw           # fit problems, no notes
    python run_models.py fit_val           # fit problems, each with ITS OWN Procedure (validation)
    python run_models.py A|A2|B|C|D|E      # test problems; notes from runs/notes_<arm>.json

Resumable (an existing (problem, model, arm) is skipped). A test prompt byte-identical to
arm A's reuses A's graded attempt instead of re-sampling (preregistered). A2 never reuses.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import demo_env

from common import append, fit_items, grade_and_record, load_attempts, problems, sha, test_items
from models import OPEN_MODELS, SYSTEM, GeneralCompute, build_prompt


def notes_for(arm: str) -> dict:
    if arm in ("fit_raw", "A", "A2"):
        return {}
    name = "notes_fit_val.json" if arm == "fit_val" else f"notes_{arm}.json"
    return json.loads((demo_env.RUNS / name).read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm")
    ap.add_argument("--models", default=",".join(OPEN_MODELS))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    demo_env.verify_after_import()
    items = fit_items() if a.arm.startswith("fit") else test_items()
    notes = notes_for(a.arm)
    if a.arm in ("C", "Cc"):
        items = [t for t in items if t["role"] == "transfer"]
    if a.arm == "fit_val":
        items = [t for t in items if t["problem_id"] in notes]
    attempts = load_attempts()
    done = {(r["problem_id"], r["model"], r["arm"]) for r in attempts if not r.get("call_error")}
    a_rec = {(r["problem_id"], r["model"]): r for r in attempts if r["arm"] == "A" and not r.get("call_error")}
    jobs, reused = [], 0
    for t in items:
        pid = t["problem_id"]
        text = notes.get(pid, {}).get("text")
        prompt = build_prompt(problems()[pid]["prompt"], text)
        for m in a.models.split(","):
            if (pid, m, a.arm) in done:
                continue
            if a.arm not in ("fit_raw", "fit_val", "A", "A2") and not text and (pid, m) in a_rec:
                append({**a_rec[(pid, m)], "arm": a.arm, "reused_from": "A", "notes_ref": None})
                reused += 1
                continue
            jobs.append((pid, m, prompt, notes.get(pid, {}).get("ref")))
    print(f"arm {a.arm}: {len(jobs)} calls to run, {reused} reused from A", flush=True)
    gc = GeneralCompute()

    def work(job):
        pid, m, prompt, ref = job
        r = gc.complete(m, prompt)
        rec = grade_and_record(pid, m, "direct-prompt", a.arm, r.text, r.tokens_in, r.tokens_out, r.estimated,
                               r.latency_ms, error=r.error, notes_ref=ref, prompt_sha=sha(SYSTEM + prompt))
        print(f"{pid:>4} {m:<16} check={rec['check_pass']!s:<5} gold={rec['gold_pass']!s:<5} "
              f"{(rec['call_error'] or rec['eval_error'] or '')[:80]}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, jobs))
    rows = [r for r in load_attempts() if r["arm"] == a.arm and not r.get("call_error")]
    for m in a.models.split(","):
        mine = [r for r in rows if r["model"] == m]
        print(f"{m:<16} gold {sum(r['gold_pass'] for r in mine)}/{len(mine)}  check {sum(r['check_pass'] for r in mine)}/{len(mine)}"
              f"  call errors: {sum(1 for r in load_attempts() if r['arm'] == a.arm and r['model'] == m and r.get('call_error'))}")


if __name__ == "__main__":
    main()
