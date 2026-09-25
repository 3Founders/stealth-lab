"""Run AutomationBench's own CLI with a per-task memory block appended to the task's user message.

The benchmark code is not modified: `get_combined_dataset` (as imported by the eval module) is
wrapped so each row's LAST user message gets the block for that task. The system prompt is left
untouched so provider prompt caching still works. Tasks without an entry run unchanged.

    python run_arm.py --memory memory_A2_fold1.json --tasks-from split_finance_hr.json --fold 1 \
        --export-json results/A2_rep1_fold1.json -- --domains finance,hr --max-concurrent 10

Everything after `--` is passed straight to `automationbench.scripts.eval`.
memory file: {"<task_name>": "<memory text>", ...}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AB = Path(__file__).resolve().parents[2] / "AutomationBench"
sys.path.insert(0, str(AB))

HEADER = ("\n\n---\nNotes from previous work on similar tasks (may or may not apply; verify against "
          "the current data before relying on them):\n")


def _with_memory(dataset, memory: dict[str, str]):
    def add(row):
        info = json.loads(row["info"]) if isinstance(row["info"], str) else row["info"]
        note = memory.get(info.get("task_name"))
        if not note:
            return row
        prompt = [dict(m) for m in row["prompt"]]
        for m in reversed(prompt):
            if m.get("role") == "user":
                m["content"] = f"{m['content']}{HEADER}{note.strip()}"
                break
        row["prompt"] = prompt
        return row
    return dataset.map(add)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", required=True)
    ap.add_argument("--tasks-from", help="split JSON; with --fold, restricts the run to that fold's tasks")
    ap.add_argument("--fold", type=int)
    ap.add_argument("--export-json", required=True)
    a, rest = ap.parse_known_args()
    rest = [x for x in rest if x != "--"]

    memory = json.loads(Path(a.memory).read_text(encoding="utf-8"))
    import automationbench.scripts.eval as ab_eval

    original = ab_eval.get_combined_dataset
    ab_eval.get_combined_dataset = lambda domains: _with_memory(original(domains), memory)

    argv = ["auto-bench", "--export-json", a.export_json, *rest]
    if a.tasks_from is not None and a.fold is not None:
        tasks = json.loads(Path(a.tasks_from).read_text())["fold_tasks"][a.fold]
        argv += ["--tasks", ",".join(tasks)]
    sys.argv = argv
    ab_eval.main()


if __name__ == "__main__":
    main()
