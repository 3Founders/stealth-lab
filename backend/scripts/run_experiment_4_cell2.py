"""
Experiment 4, Cell 2: SLM (qwen/qwen3.6-27b via Groq) given the same
edit-pdf instruction as Cell 1, PLUS a real retrieved TaskNode
trajectory hint -- the 'pdf' skill's actual ingested content (name,
description, postconditions).

HONEST SCOPE NOTE: see fetch_pdf_skill_trajectory() in
experiment_4_common.py -- the original plan described this hint as
"masked io_schema, ordered REQUIRES chain, preconditions/postconditions."
Real data doesn't support that; this uses what's actually there.

One thing worth flagging plainly: the retrieved description explicitly
mentions redaction ("redact sensitive or authorship-leaking content...
prove that a produced PDF is readable and non-recoverable where
redaction is required") -- directly relevant to Cell 1's real failure
(it overlaid new text without redacting the old placeholder data). This
is a real property of the retrieved data, not something engineered
into the prompt for this specific test.

Requires: GROQ_API_KEY and DATABASE_URL in backend/.env (needs the DB
for the real trajectory fetch, unlike Cell 1), plus
    pip install groq PyMuPDF pypdf pytest --break-system-packages

Run from backend/:
    python scripts/run_experiment_4_cell2.py
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_slm, fetch_pdf_skill_trajectory, fetch_task_files, run_cell

TASK_ROOT = Path("after_task_root_cell2")


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
        cell_label="Cell 2: SLM (qwen/qwen3.6-27b), with TaskNode trajectory hint",
        call_model_fn=call_slm,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
