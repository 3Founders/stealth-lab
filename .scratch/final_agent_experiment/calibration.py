"""
Non-scored step-budget calibration for T1/T3/T7, both arms.
Reuses run_one_trial/orchestrator machinery exactly like run_pilot.py, but
writes to calibration/raw/ (never trials.jsonl) and every record carries
scored=False. Not part of the 12-trial pilot; produces no gradeable result.
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
OUT_DIR = Path(__file__).resolve().parent / "calibration"
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "final_agent_calibration_trials"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

TASKS = {}
with open(Path(__file__).resolve().parent / "tasks.jsonl", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        TASKS[row["task_id"]] = row

CAL_TASK_IDS = ["T1", "T3", "T7"]
MAX_STEPS_CANDIDATE = int(sys.argv[1]) if len(sys.argv) > 1 else 16
TIME_BUDGET_S = max(600, MAX_STEPS_CANDIDATE * 60)  # scale outer ceiling with step count


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    port = 8814
    env = os.environ.copy()
    proc = _start_mcp_server(REPO_ROOT, port, env)
    server_url = f"http://127.0.0.1:{port}/mcp"
    time.sleep(5)
    if proc.poll() is not None:
        out = proc.stdout.read() if proc.stdout else ""
        print(f"MCP SERVER FAILED TO START: {out[-3000:]}")
        return 1

    results = []
    try:
        plan = [(t, a) for t in CAL_TASK_IDS for a in ("A", "B_default")]
        for i, (task_id, arm) in enumerate(plan):
            task = TASKS[task_id]
            print(f"[{i+1}/{len(plan)}] CALIBRATION max_steps={MAX_STEPS_CANDIDATE} "
                  f"task={task_id} arm={arm} starting...", flush=True)
            t0 = time.monotonic()
            record = await run_one_trial(
                task_id=task_id, task_description=task["statement"], arm=arm,
                frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT, tmp_root=TMP_ROOT,
                model="gpt-oss-120b", max_steps=MAX_STEPS_CANDIDATE, time_budget_s=TIME_BUDGET_S,
                server_url=server_url if arm != "A" else None,
                token=token if arm != "A" else None,
                out_dir=OUT_DIR, scored=False,
                verify_fn=VERIFIERS[task_id],
            )
            record["calibration_max_steps"] = MAX_STEPS_CANDIDATE
            results.append(record)
            dt = time.monotonic() - t0
            steps_used = None
            notes = record.get("node_notes") or []
            for n in notes:
                if "tool_calls=" in n:
                    try:
                        steps_used = int(n.split("tool_calls=")[1].split()[0])
                    except Exception:
                        pass
            print(f"[{i+1}/{len(plan)}] done in {dt:.1f}s success={record.get('task_success')} "
                  f"steps_used~={steps_used} budget_exceeded={record.get('budget_exceeded')} "
                  f"error={record.get('error')}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    out_path = OUT_DIR / f"calibration_max_steps_{MAX_STEPS_CANDIDATE}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"\nWrote {len(results)} calibration records to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
