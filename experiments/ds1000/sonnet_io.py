"""Sonnet via fresh subagents.

prepare: batch INPUT files (prompts only -- no tests, no references) under runs/sonnet_in/.
         A batch never holds two problems of the same family and never mixes arms.
record:  grade each OUTPUT file (runs/sonnet_out/<same name>) locally; tokens estimated.

    python sonnet_io.py prepare fit_raw --batch 6
    python sonnet_io.py prepare A --batch 8
    python sonnet_io.py record A
"""
from __future__ import annotations

import argparse
import json

import demo_env

from common import append, fit_items, grade_and_record, load_attempts, problems, sha, test_items
from models import SONNET, SYSTEM, build_prompt, estimate_tokens
from run_models import notes_for

IN_DIR = demo_env.RUNS / "sonnet_in"
OUT_DIR = demo_env.RUNS / "sonnet_out"
SCAFFOLD = "claude-code-subagent"


def _items(arm: str) -> list[dict]:
    return fit_items() if arm.startswith("fit") else test_items()


def prepare(arm: str, batch: int) -> list[str]:
    notes = notes_for(arm)
    attempts = load_attempts()
    done = {r["problem_id"] for r in attempts if r["model"] == SONNET and r["arm"] == arm}
    a_rec = {r["problem_id"]: r for r in attempts if r["model"] == SONNET and r["arm"] == "A"}
    todo = []
    for t in _items(arm):
        pid = t["problem_id"]
        if pid in done:
            continue
        text = notes.get(pid, {}).get("text")
        if arm in ("B", "Bc", "Bw", "K") and not text and pid in a_rec:
            append({**a_rec[pid], "arm": arm, "reused_from": "A", "notes_ref": None})
            continue
        todo.append(t)
    # greedy batching: no two problems of one family in a batch
    batches: list[list[dict]] = []
    for t in todo:
        home = next((b for b in batches if len(b) < batch and all(x["family"] != t["family"] for x in b)), None)
        (home.append(t) if home is not None else batches.append([t]))
    IN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    offset = len(list(IN_DIR.glob(f"{arm}_*.json")))      # never overwrite an earlier batch
    for i, b in enumerate(batches, start=offset):
        name = f"{arm}_{i:02d}.json"
        tasks = {t["problem_id"]: build_prompt(problems()[t["problem_id"]]["prompt"], notes.get(t["problem_id"], {}).get("text"))
                 for t in b}
        (IN_DIR / name).write_text(json.dumps({"instructions": SYSTEM, "tasks": tasks}, indent=1), encoding="utf-8")
        names.append(name)
    return names


def record(arm: str) -> None:
    notes = notes_for(arm)
    done = {r["problem_id"] for r in load_attempts() if r["model"] == SONNET and r["arm"] == arm}
    for path in sorted(OUT_DIR.glob(f"{arm}_*.json")):
        answers = json.loads(path.read_text(encoding="utf-8"))
        prompts = json.loads((IN_DIR / path.name).read_text(encoding="utf-8"))["tasks"]
        for pid, text in answers.items():
            if pid in done or pid not in prompts:
                continue
            rec = grade_and_record(pid, SONNET, SCAFFOLD, arm, text, estimate_tokens(SYSTEM + prompts[pid]),
                                   estimate_tokens(text), True, 0, notes_ref=notes.get(pid, {}).get("ref"),
                                   prompt_sha=sha(SYSTEM + prompts[pid]))
            done.add(pid)
            print(f"{pid:>4} sonnet check={rec['check_pass']!s:<5} gold={rec['gold_pass']!s:<5} {(rec['eval_error'] or '')[:80]}")
    mine = [r for r in load_attempts() if r["model"] == SONNET and r["arm"] == arm]
    expected = len(_items(arm))
    print(f"sonnet {arm}: gold {sum(r['gold_pass'] for r in mine)}/{len(mine)} (expected {expected})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["prepare", "record"])
    ap.add_argument("arm", choices=["fit_raw", "A", "B", "Bc", "Bw", "K"])
    ap.add_argument("--batch", type=int, default=6)
    a = ap.parse_args()
    if a.action == "prepare":
        print("\n".join(prepare(a.arm, a.batch)))
    else:
        record(a.arm)
