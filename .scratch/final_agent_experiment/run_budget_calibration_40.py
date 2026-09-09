"""
Step-budget calibration at max_steps=40 (final pre-score remediation,
section C, step 2). Non-scored. Rule frozen in step-budget-calibration.md
BEFORE this script was run. Starts ONE real MCP server, runs 6 real
calibration trials (T1/T3/T7-v2 x {A, B_default}) sequentially against it.
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
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "calib40"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

TASKS = {
    "T1": "In this repository at commit bd768e62a887b13a94fdd118693a5c671df1cf95, examine backend/app/mcp_server/server.py and answer: (a) how many functions are decorated with @server.tool(), listing their exact names, and (b) which single function is the SHARED resolver that get_procedure, check_procedure, check_applicability, report_execution, and decide_procedure all use to accept either a procedures.procedure_id family handle or a procedures.id row key. Write the answer to answer.md as a short numbered list with exact function names and line numbers.",
    "T3": "backend/app/services/procedure_extraction/capability.py defines a function used by at least one other module in backend/app/services/. Find every real call site of that function across the whole backend/app/ tree, rename it to compute_wilson_lower_bound everywhere (definition and every call site), and confirm the existing test suite for that area still passes.",
    "T7-v2": "Across every top-level .py file directly under backend/app/services/ (NOT subdirectories), find the single function or method (including methods defined inside a class) with the largest number of physical lines in its body, counted from its `def` line through its last body line inclusive. Report in answer.md: the function/method name, its containing file (relative path), and its exact line count. Use as few full-file reads as you reasonably can; prefer targeted search/outline tools over reading every file in full.",
}
CELLS = [(t, a) for t in TASKS for a in ("A", "B_default")]
RESULTS_PATH = OUT_DIR / "calibration_max_steps_40.jsonl"


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()  # load into THIS process's real env too --
            # run_trial_arm_A/B call the real Agent.run() in-process, which reads
            # os.environ directly (via app.config.settings) -- a separate `env` dict
            # built only for the subprocess never reaches that in-process code path.
    os.environ["STEALTHLAB_MCP_TOKEN"] = "bDBkrdBsLe7YJCu8Gh-dxUuL9DK_OF6bD2K7RhDu_eA"
    env = os.environ.copy()
    token = env["STEALTHLAB_MCP_TOKEN"]

    port = 8820
    proc = _start_mcp_server(REPO_ROOT, port, env)
    try:
        await asyncio.sleep(25.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server exited early: {out}")
        server_url = f"http://127.0.0.1:{port}/mcp"

        done_cells = set()
        if RESULTS_PATH.exists():
            for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    done_cells.add((r["task_id"], r["arm"]))

        with open(RESULTS_PATH, "a", encoding="utf-8") as outf:
            for task_id, arm in CELLS:
                if (task_id, arm) in done_cells:
                    print(f"skip {task_id}/{arm}, already done", flush=True)
                    continue
                t0 = time.time()
                print(f"=== {task_id}/{arm} starting ===", flush=True)
                record = await run_one_trial(
                    task_id=task_id, task_description=TASKS[task_id], arm=arm,
                    frozen_commit="bd768e62a887b13a94fdd118693a5c671df1cf95",
                    repo_root=REPO_ROOT, tmp_root=TMP_ROOT, model="gpt-oss-120b",
                    max_steps=40, time_budget_s=900,
                    server_url=server_url if arm != "A" else None,
                    token=token if arm != "A" else None,
                    out_dir=OUT_DIR, scored=False,
                )
                outf.write(json.dumps(record, default=str) + "\n")
                outf.flush()
                elapsed = time.time() - t0
                notes = record.get("node_notes") or [record.get("notes", "")]
                print(f"=== {task_id}/{arm} done in {elapsed:.1f}s: {notes} ===", flush=True)
        print("all cells complete", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
