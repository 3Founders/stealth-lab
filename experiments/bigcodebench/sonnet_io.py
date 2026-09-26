"""Sonnet-as-a-subagent plumbing.

prepare: write batch INPUT files (task statements only -- no tests, no solutions) that a
         fresh Sonnet subagent reads:  runs/sonnet_in/<split>_<condition>_<n>.json
record:  grade each subagent's OUTPUT file (runs/sonnet_out/<same name>) locally and
         append the attempts (tokens estimated from text length, flagged).

    python sonnet_io.py prepare --split fit --condition raw --batch 5
    python sonnet_io.py record  --split fit --condition raw
"""
from __future__ import annotations

import argparse
import json

import demo_env

from models import SCAFFOLD_SUBAGENT, SYSTEM, build_prompt, estimate_tokens
from run_models import grade_and_record, load_attempts, sample_tasks

MODEL = "claude-sonnet-5"
REUSE = False
IN_DIR = demo_env.RUNS / "sonnet_in"
OUT_DIR = demo_env.RUNS / "sonnet_out"


def prepare(split: str, condition: str, batch: int) -> list[str]:
    tasks = sample_tasks(split)
    knowledge = {}
    if condition != "raw":
        name = f"knowledge_{split}.json" if condition == "kel" else f"knowledge_{split}_down.json"
        knowledge = json.loads((demo_env.RUNS / name).read_text(encoding="utf-8"))
    done = {(r["task_id"], r["condition"]) for r in load_attempts() if r["model"] == MODEL}
    todo = [t for t in tasks.values() if (t.external_id, condition) not in done]
    if condition != "raw" and REUSE:
        from run_models import append
        raw = {r["task_id"]: r for r in load_attempts() if r["model"] == MODEL and r["condition"] == "raw" and r["split"] == split}
        keep = []
        for t in todo:
            if knowledge.get(t.external_id, {}).get("text") is None and t.external_id in raw:
                append({**raw[t.external_id], "condition": condition, "reused_from_raw": True, "knowledge_ref": None})
            else:
                keep.append(t)
        todo = keep
    IN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(0, len(todo), batch):
        name = f"{split}_{condition}_{i // batch}.json"
        chunk = {t.external_id: build_prompt(t.goal_description, knowledge.get(t.external_id, {}).get("text"))
                 for t in todo[i:i + batch]}
        (IN_DIR / name).write_text(json.dumps({"instructions": SYSTEM, "tasks": chunk}, indent=1), encoding="utf-8")
        names.append(name)
    return names


def record(split: str, condition: str) -> None:
    tasks = sample_tasks(split)
    knowledge = {}
    if condition != "raw":
        name = f"knowledge_{split}.json" if condition == "kel" else f"knowledge_{split}_down.json"
        knowledge = json.loads((demo_env.RUNS / name).read_text(encoding="utf-8"))
    done = {(r["task_id"], r["condition"]) for r in load_attempts() if r["model"] == MODEL}
    for path in sorted(OUT_DIR.glob(f"{split}_{condition}_*.json")):
        answers = json.loads(path.read_text(encoding="utf-8"))
        prompts = json.loads((IN_DIR / path.name).read_text(encoding="utf-8"))["tasks"]
        for task_id, text in answers.items():
            if task_id not in tasks or (task_id, condition) in done or task_id not in prompts:
                continue
            rec = grade_and_record(
                tasks[task_id], split, MODEL, SCAFFOLD_SUBAGENT, condition, text,
                estimate_tokens(SYSTEM + prompts[task_id]), estimate_tokens(text), True, 0,
                knowledge_ref=knowledge.get(task_id, {}).get("ref"))
            done.add((task_id, condition))
            print(f"{task_id:<18} sonnet visible={rec['visible_pass']!s:<5} gold={rec['gold_pass']!s:<5} "
                  f"{rec['eval_error'] or ''}"[:150])
    mine = [r for r in load_attempts() if r["model"] == MODEL and r["split"] == split and r["condition"] == condition]
    print(f"sonnet gold {sum(r['gold_pass'] for r in mine)}/{len(mine)} visible {sum(r['visible_pass'] for r in mine)}/{len(mine)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["prepare", "record"])
    ap.add_argument("--split", choices=["fit", "heldout"], required=True)
    ap.add_argument("--condition", choices=["raw", "kel", "kel_down"], default="raw")
    ap.add_argument("--reuse-raw", action="store_true")
    ap.add_argument("--batch", type=int, default=5)
    a = ap.parse_args()
    REUSE = a.reuse_raw
    if a.action == "prepare":
        print("\n".join(prepare(a.split, a.condition, a.batch)))
    else:
        record(a.split, a.condition)
