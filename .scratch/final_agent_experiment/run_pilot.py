"""
Runs the pre-registered 12-trial pilot: T1/T3/T7 x 2 trials x {A, B_default}.
Sequential (no parallel trials -- isolation via disposable worktrees, one at
a time). Counterbalances arm order per trial index. Starts one real MCP
server for the whole run (repo_path is per-call, not per-server-instance --
confirmed by reading LocalAgentRunner.run()'s signature), reused across all
B trials; arm A never touches it at all.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from orchestrator import run_one_trial, _start_mcp_server  # noqa: E402
from verifiers import VERIFIERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_COMMIT = "bd768e62a887b13a94fdd118693a5c671df1cf95"
OUT_DIR = Path(__file__).resolve().parent
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "final_agent_pilot_trials"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

TASKS = {}
with open(OUT_DIR / "tasks.jsonl", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        TASKS[row["task_id"]] = row

PILOT_TASK_IDS = ["T1", "T3", "T7"]

# 12 cells: (task, arm) x 2 trials each, order counterbalanced by
# alternating which arm goes first within each (task, trial_index) pair.
PLAN = []
for task_id in PILOT_TASK_IDS:
    for trial_idx in range(2):
        arms = ["A", "B_default"] if trial_idx % 2 == 0 else ["B_default", "A"]
        for arm in arms:
            PLAN.append((task_id, arm, trial_idx))


async def main():
    # BUGFIX (applied before any trial in this run): the first attempt at
    # this pilot loaded .env into a dict handed only to the MCP-server
    # SUBPROCESS, never into THIS process's own os.environ -- so arm A
    # (which calls runner._run_local_node in-process, no subprocess) failed
    # immediately with KeyError('GENERAL_COMPUTE_API_KEY') before any real
    # API call, and arm B's in-process retrieval/embedding calls failed the
    # same way inside an ExceptionGroup. Zero tokens were spent on any of
    # those failed attempts (confirmed: every failure happened before
    # Agent.run() was ever reached). Fix: load the full .env into THIS
    # process's os.environ FIRST, so both this process (arm A, and arm B's
    # in-process retrieval) and the server subprocess have everything.
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

    server_url = None
    token = os.environ.get("STEALTHLAB_MCP_TOKEN")

    port = 8813
    env = os.environ.copy()
    proc = _start_mcp_server(REPO_ROOT, port, env)
    server_url = f"http://127.0.0.1:{port}/mcp"
    time.sleep(5)  # let uvicorn bind
    if proc.poll() is not None:
        out = proc.stdout.read() if proc.stdout else ""
        print(f"MCP SERVER FAILED TO START: {out[-3000:]}")
        return 1

    results = []
    try:
        for i, (task_id, arm, trial_idx) in enumerate(PLAN):
            task = TASKS[task_id]
            print(f"[{i+1}/12] task={task_id} arm={arm} trial_idx={trial_idx} starting...", flush=True)
            t0 = time.monotonic()
            record = await run_one_trial(
                task_id=task_id, task_description=task["statement"], arm=arm,
                frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT, tmp_root=TMP_ROOT,
                model="gpt-oss-120b", max_steps=8, time_budget_s=600,
                server_url=server_url if arm != "A" else None,
                token=token if arm != "A" else None,
                out_dir=OUT_DIR, scored=True,
                verify_fn=VERIFIERS[task_id],
            )
            record["trial_index_within_cell"] = trial_idx
            results.append(record)
            dt = time.monotonic() - t0
            print(f"[{i+1}/12] done in {dt:.1f}s success={record.get('task_success')} "
                  f"tokens={record.get('total_tokens')} error={record.get('error')} "
                  f"budget_exceeded={record.get('budget_exceeded')}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Prepend the 5 pre-fix environmental-failure attempts (real raw records
    # already on disk from the killed first run) so they are never silently
    # dropped -- tagged environmental_failure, excluded from the 12 scored
    # trials, but visible in the permanent record per protocol.md's own
    # environmental-failure-handling rule.
    prefix_records = []
    for fname in ["T1-A-26f1c4c2.json", "T1-A-4c219301.json",
                  "T1-B_default-0f6212b4.json", "T1-B_default-de2295d3.json",
                  "T3-A-c72e9f33.json"]:
        p = OUT_DIR / "raw" / fname
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["environmental_failure"] = True
            rec["environmental_failure_reason"] = (
                "pre-fix run_pilot.py bug: .env was loaded only into the MCP-server "
                "subprocess's env dict, never into this orchestrator process's own "
                "os.environ, so GENERAL_COMPUTE_API_KEY was missing for in-process "
                "calls (arm A always, arm B's in-process retrieval/embedding calls). "
                "Zero tokens spent -- every failure occurred before any Agent.run() "
                "call. Fixed by loading the full .env into os.environ at the very "
                "start of main(), before this replacement run below."
            )
            rec["superseded_by_scored_replacement"] = True
            prefix_records.append(rec)

    with open(OUT_DIR / "trials.jsonl", "w", encoding="utf-8") as f:
        for r in prefix_records + results:
            f.write(json.dumps(r, default=str) + "\n")

    print(f"\nWrote {len(prefix_records)} pre-fix environmental-failure records "
          f"+ {len(results)} scored trial records to trials.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
