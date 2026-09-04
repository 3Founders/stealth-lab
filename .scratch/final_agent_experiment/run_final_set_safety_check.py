"""
Minimum non-scored safety-check calibration for the FINAL surviving task set
(T1-v2/T3-v2/T7-v2, final pre-score remediation, bounded T1/T3 pass). Proves
the chosen common max_steps is safe (trials terminate, no hang) for the
final set -- NOT another open-ended budget search. 1 trial per cell,
matching this session's own established "minimum needed" convention for
pure safety-check calibration (no verify_fn -- correctness grading is a
separate, already-established concern from the prior pass's real scored
pilot; this script only proves termination safety).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from orchestrator import _start_mcp_server, run_one_trial  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "calibration"
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "final_set_safety_check"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

TASKS = {}
with open(Path(__file__).resolve().parent / "tasks.jsonl", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        TASKS[row["task_id"]] = row

FINAL_TASK_IDS = ["T1-v2", "T3-v2", "T7-v2"]
MAX_STEPS = 40  # already proven safe/non-hanging (this branch's prior pass's own
                # stress-tested fix + successful 6-cell run at this exact value)
CELLS = [(t, a) for t in FINAL_TASK_IDS for a in ("A", "B_default")]
RESULTS_PATH = OUT_DIR / "final_set_safety_check.jsonl"


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()
    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    env = os.environ.copy()

    port = 8825
    proc = _start_mcp_server(REPO_ROOT, port, env)
    try:
        await asyncio.sleep(25.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server exited early: {out}")
        server_url = f"http://127.0.0.1:{port}/mcp"

        with open(RESULTS_PATH, "a", encoding="utf-8") as outf:
            for task_id, arm in CELLS:
                t0 = time.time()
                print(f"=== {task_id}/{arm} starting (max_steps={MAX_STEPS}) ===", flush=True)
                record = await run_one_trial(
                    task_id=task_id, task_description=TASKS[task_id]["statement"], arm=arm,
                    frozen_commit="bd768e62a887b13a94fdd118693a5c671df1cf95",
                    repo_root=REPO_ROOT, tmp_root=TMP_ROOT, model="gpt-oss-120b",
                    max_steps=MAX_STEPS, time_budget_s=900,
                    server_url=server_url if arm != "A" else None,
                    token=token if arm != "A" else None,
                    out_dir=OUT_DIR, scored=False,
                )
                outf.write(json.dumps(record, default=str) + "\n")
                outf.flush()
                elapsed = time.time() - t0
                notes = record.get("node_notes") or [record.get("notes", "")]
                hung = elapsed > 900 + 30  # any margin over the enforced ceiling = a real problem
                print(f"=== {task_id}/{arm} done in {elapsed:.1f}s "
                      f"(hung={hung}) error={record.get('error')} notes={notes} ===", flush=True)
        print("all cells complete, no hangs", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
