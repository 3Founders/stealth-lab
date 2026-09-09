"""
FINAL TASK-SET DECISION pass (this task only). Verifies the one-shot
revisions T1-v3 and T3-v3, plus a confirmation re-run of T7-v2 (already
known-good, cheap to re-confirm rather than assumed unchanged), each exactly
once per arm -- unless a cell hits a clear provider/API error, in which case
exactly one retry is allowed (same retry discipline as the prior
quick_confirmation_check.py), never more, never to force a favorable
outcome.

Reuses orchestrator.run_one_trial / _start_mcp_server and verifiers.VERIFIERS
unmodified. max_steps stays 40 -- the already-frozen value; this pass does
not run another calibration search.

Non-scored (scored=False) throughout.
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
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "final_taskset_verification"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

with open(Path(__file__).resolve().parent / "tasks.jsonl", encoding="utf-8") as f:
    TASKS = {json.loads(line)["task_id"]: json.loads(line) for line in f}

MAX_STEPS = 40  # frozen, unchanged -- no calibration search this pass
CELLS = [
    ("T1-v3", "A"), ("T1-v3", "B_default"),
    ("T3-v3", "A"), ("T3-v3", "B_default"),
    ("T7-v2", "A"), ("T7-v2", "B_default"),
]
RESULTS_PATH = OUT_DIR / "final_taskset_verification_results.jsonl"


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

    port = 8827
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
                        break  # a real, non-provider-noise outcome -- that IS the data point
                    if attempt == 2:
                        print(f"=== {cell_name}: 2 consecutive provider errors, STOPPING this cell "
                              f"per retry discipline ===", flush=True)
                outf.write(json.dumps(final_record, default=str) + "\n")
                outf.flush()
        print("final task-set verification complete", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
