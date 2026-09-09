"""
ONE non-scored T7-v2 / Arm A end-to-end confirmation run.

Real model (GENERAL_COMPUTE gpt-oss-120b), real Agent+RepoSandbox, real
disposable git worktree pinned to the same frozen commit, max_steps=40,
the frozen T7-v2 statement read straight out of tasks.jsonl -- and now the
real T7-v2 verifier, wired through VERIFIERS, which is the thing this pass
fixed.

Arm A only. scored=False, and the output goes to its own directory so it
can never be mistaken for scored-matrix data. Arm A by construction
bypasses MCP entirely (orchestrator.run_trial_arm_A calls
runner._run_local_node directly, server_url=None), so no MCP server is
started here -- starting one would not change a single byte of what arm A
does.

Run from anywhere:  python run_t7v2_grading_confirmation.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from orchestrator import run_one_trial  # noqa: E402
from verifiers import VERIFIERS  # noqa: E402

FROZEN_COMMIT = "bd768e62a887b13a94fdd118693a5c671df1cf95"
TASK_ID = "T7-v2"
ARM = "A"
MAX_STEPS = 40
TIME_BUDGET_S = 900
OUT_DIR = HERE / "t7v2_grading_confirmation"
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "t7v2-confirmation"


def _load_env() -> None:
    env_path = REPO_ROOT / "backend" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    _load_env()
    TMP_ROOT.mkdir(parents=True, exist_ok=True)

    tasks = {}
    with open(HERE / "tasks.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            tasks[row["task_id"]] = row
    task = tasks[TASK_ID]

    verify_fn = VERIFIERS.get(TASK_ID)
    if verify_fn is None:
        print("ABORT: no verifier registered for " + TASK_ID)
        return 1

    print("=== %s/%s NON-SCORED confirmation, max_steps=%d ===" % (TASK_ID, ARM, MAX_STEPS),
          flush=True)
    t0 = time.time()
    record = await run_one_trial(
        task_id=TASK_ID, task_description=task["statement"], arm=ARM,
        frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT, tmp_root=TMP_ROOT,
        model="gpt-oss-120b", max_steps=MAX_STEPS, time_budget_s=TIME_BUDGET_S,
        server_url=None, token=None,
        out_dir=OUT_DIR, scored=False, verify_fn=verify_fn,
    )
    print("=== done in %.1fs ===" % (time.time() - t0), flush=True)

    for key in ("trial_id", "task_success", "deterministic_correctness",
                "failure_category", "files_touched", "tool_calls", "model_calls",
                "input_tokens", "output_tokens", "budget_exceeded", "error", "notes"):
        print("%-28s %s" % (key, record.get(key)))
    print("verification_quality        %s" % json.dumps(record.get("verification_quality")))
    print("tool_names                  %s" % (record.get("tool_names") or []))
    print("-- final_message ------------------------------------------------")
    # A real model reply routinely contains characters (non-breaking hyphens,
    # smart quotes) that a Windows cp1252 console cannot encode -- printing it
    # raw crashed this script AFTER a real, already-recorded trial. The JSON
    # record is written before this point either way, but a diagnostic printer
    # must never be the thing that fails.
    msg = record.get("final_message") or "(none)"
    enc = sys.stdout.encoding or "utf-8"
    print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))
    print("-----------------------------------------------------------------")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
