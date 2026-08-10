"""
Full-system test on Task C: a THIRD instance in the same family
(group-by -> exclude-by-status -> sum+count -> round -> sort -> JSON),
retrieved against the same real library used for Task B. This is the
actual test of whether "one real solved example helps a new similar
task" generalizes beyond a single pair, or was specific to Task A/B.

Uses Task A's ORIGINAL trajectory (not any debate-merged content --
per the pragmatic decision after 3 real runs showed the debate
mechanism isn't currently reliable at synthesis between near-duplicate
candidates; that remains an open, unresolved question, not silently
assumed fixed).

Run from backend/ (after ingest_trajectory_library.py):
    python scripts/synthetic_tasks/run_task_c_full_system.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from full_system_common import run_full_system
from retrieve_trajectory import EXPERIMENTAL_THRESHOLD_OVERRIDE
import task_c.generate as gen_c

HERE = Path(__file__).parent


async def main():
    return await run_full_system(
        "Task C", "task_c", "shipments.csv", gen_c, HERE,
        retrieval_threshold=EXPERIMENTAL_THRESHOLD_OVERRIDE,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
