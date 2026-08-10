"""
Experiment 4, Cell 4: frontier-adjacent LLM (gpt-oss-120b via General
Compute) with the real TaskNode trajectory hint (same real 'pdf' skill
content Cell 2 used). The control cell of the 2x2 -- the hypothesis
predicts this should barely move from Cell 3, since the model is
expected to already be near-ceiling without help.

HONEST NOTE: switched from Anthropic to General Compute (no Anthropic
credits available). See call_llm()'s docstring in
experiment_4_common.py for the full reasoning on this substitution.

Uses experiment_4_common.py for the shared pipeline.

Requires: GENERAL_COMPUTE_API_KEY and DATABASE_URL in backend/.env, plus
    pip install openai PyMuPDF pypdf pytest --break-system-packages

Run from backend/:
    python scripts/run_experiment_4_cell4.py
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_llm, fetch_pdf_skill_trajectory, fetch_task_files, run_cell

TASK_ROOT = Path("after_task_root_cell4")


async def main() -> int:
    if TASK_ROOT.exists():
        shutil.rmtree(TASK_ROOT)
    TASK_ROOT.mkdir(parents=True)

    print("[0/6] fetching the real TaskNode trajectory hint from the DB...")
    trajectory = await fetch_pdf_skill_trajectory()
    print(trajectory)

    print("[1/6] fetching real task files...")
    instruction = fetch_task_files(TASK_ROOT)

    user_message = f"{trajectory}\n---\n\n{instruction}"

    return await run_cell(
        TASK_ROOT, user_message,
        cell_label="Cell 4: LLM (gpt-oss-120b via General Compute), with TaskNode trajectory hint",
        call_model_fn=call_llm,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
