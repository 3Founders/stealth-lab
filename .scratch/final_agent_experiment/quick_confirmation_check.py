"""
FINAL QUICK CONFIRMATION (coordinator task, this pass only). Resolves the
3 missing cells from the prior 6-cell safety check (T1-v2/A, T1-v2/B_default,
T3-v2/A all hit api_error/never reached natural completion there) -- proves
whether the repaired graders can actually evaluate a completed run under the
frozen setup. T3-v2/B_default is NOT rerun -- already verified in the prior
pass (real completion via step_budget, no error).

Reuses orchestrator.run_one_trial / _start_mcp_server and verifiers.VERIFIERS
unmodified. Retry discipline: at most 2 attempts per cell; if the 2nd attempt
also errors, STOP that cell (do not keep retrying for a forced success).
Non-scored (scored=False) throughout -- verify_fn is still passed so the
REAL grader runs against a REAL completed worktree when one is reached, as
purely diagnostic evidence for this readiness question, not as a scored
result feeding the frozen protocol's own pilot.
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
from verifiers import VERIFIERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "calibration"
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "quick_confirmation_check"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

with open(Path(__file__).resolve().parent / "tasks.jsonl", encoding="utf-8") as f:
    TASKS = {json.loads(line)["task_id"]: json.loads(line) for line in f}

MAX_STEPS = 40  # frozen value, unchanged, per the coordinator's explicit "do not change max_steps"
CELLS = [("T1-v2", "A"), ("T1-v2", "B_default"), ("T3-v2", "A")]
RESULTS_PATH = OUT_DIR / "quick_confirmation_results.jsonl"


def _is_provider_error(record: dict) -> bool:
    err = (record.get("error") or "").lower()
    notes = record.get("node_notes") or [record.get("notes", "")]
    note_text = " ".join(notes).lower()
    return ("api_error" in note_text or "apierror" in err or "connectionerror" in err
            or "timeout" in err or "rate limit" in note_text or "rate_limit" in note_text)


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()
    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    env = os.environ.copy()

    port = 8826
    proc = _start_mcp_server(REPO_ROOT, port, env)
    try:
        await asyncio.sleep(25.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server exited early: {out}")
        server_url = f"http://127.0.0.1:{port}/mcp"

        with open(RESULTS_PATH, "a", encoding="utf-8") as outf:
            for task_id, arm in CELLS:
                cell_name = f"{task_id}/{arm}"
                verify_fn = VERIFIERS[task_id]
                final_record = None
                for attempt in (1, 2):
                    t0 = time.time()
                    print(f"=== {cell_name} attempt {attempt}/2 (max_steps={MAX_STEPS}) ===", flush=True)
                    record = await run_one_trial(
                        task_id=task_id, task_description=TASKS[task_id]["statement"], arm=arm,
                        frozen_commit="bd768e62a887b13a94fdd118693a5c671df1cf95",
                        repo_root=REPO_ROOT, tmp_root=TMP_ROOT, model="gpt-oss-120b",
                        max_steps=MAX_STEPS, time_budget_s=900,
                        server_url=server_url if arm != "A" else None,
                        token=token if arm != "A" else None,
                        out_dir=OUT_DIR, scored=False, verify_fn=verify_fn,
                    )
                    record["attempt"] = attempt
                    elapsed = time.time() - t0
                    hung = elapsed > 900 + 30
                    provider_err = _is_provider_error(record)
                    print(f"=== {cell_name} attempt {attempt} done in {elapsed:.1f}s "
                          f"hung={hung} provider_error={provider_err} error={record.get('error')} "
                          f"task_success={record.get('task_success')} "
                          f"verification_quality={record.get('verification_quality')} ===", flush=True)
                    final_record = record
                    if not provider_err:
                        break  # got a real, non-provider-noise outcome (natural stop, step_budget,
                               # or a genuine non-provider error) -- that IS the data point, don't retry
                    if attempt == 2:
                        print(f"=== {cell_name}: 2 consecutive provider errors, STOPPING this cell "
                              f"per retry discipline (not retrying further) ===", flush=True)
                outf.write(json.dumps(final_record, default=str) + "\n")
                outf.flush()
        print("quick confirmation check complete", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
