"""
Experiment 4, Cell 1: SLM (qwen/qwen3.6-27b via Groq) given the raw
edit-pdf instruction, no TaskNode trajectory hint. Baseline cell of the
2x2 design -- what does the SLM do with nothing but the plain task
description?

Uses experiment_4_common.py for the shared pipeline (fetch, call, verify)
-- this cell only supplies WHICH user_message to send.

Requires: GROQ_API_KEY in backend/.env, plus
    pip install groq PyMuPDF pypdf pytest --break-system-packages

Run from backend/:
    python scripts/run_experiment_4_cell1.py
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_slm, fetch_task_files, run_cell

TASK_ROOT = Path("after_task_root_cell1")


async def main() -> int:
    if TASK_ROOT.exists():
        shutil.rmtree(TASK_ROOT)
    TASK_ROOT.mkdir(parents=True)

    print("[1/6] fetching real task files...")
    instruction = fetch_task_files(TASK_ROOT)

    return await run_cell(TASK_ROOT, instruction, cell_label="Cell 1: SLM (qwen/qwen3.6-27b), no trajectory hint",
                          call_model_fn=call_slm)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
