"""
Experiment 4, Cell 3: frontier-adjacent LLM (gpt-oss-120b via General
Compute) given the raw edit-pdf instruction, no TaskNode trajectory
hint. The "reference ceiling" cell of the 2x2 -- expected to already
perform well without help, per the original hypothesis.

HONEST NOTE: switched from Anthropic to General Compute (no Anthropic
credits available). gpt-oss-120b is real and large, but an open-weight
model, not a closed-lab frontier system -- see call_llm()'s docstring
in experiment_4_common.py for the full reasoning.

Uses experiment_4_common.py for the shared pipeline -- same execution/
verification logic as Cells 1/2, just call_llm (General Compute)
instead of call_slm (Groq).

Requires: GENERAL_COMPUTE_API_KEY in backend/.env, plus
    pip install openai PyMuPDF pypdf pytest --break-system-packages

Run from backend/:
    python scripts/run_experiment_4_cell3.py
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_llm, fetch_task_files, run_cell

TASK_ROOT = Path("after_task_root_cell3")


async def main() -> int:
    if TASK_ROOT.exists():
        shutil.rmtree(TASK_ROOT)
    TASK_ROOT.mkdir(parents=True)

    print("[1/6] fetching real task files...")
    instruction = fetch_task_files(TASK_ROOT)

    return await run_cell(
        TASK_ROOT, instruction,
        cell_label="Cell 3: LLM (gpt-oss-120b via General Compute), no trajectory hint",
        call_model_fn=call_llm,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
