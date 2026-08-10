"""
Solves Task A for real, using the LLM (DeepSeek V3.2 via General
Compute) and the iterate-until-success loop, until it genuinely passes
the real verifier. The winning code + turn history becomes Task B's
real trajectory hint -- an actual solved example, not a category tag.

Run from backend/:
    python scripts/synthetic_tasks/solve_task_a.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_llm
from iterate import run_until_success
import task_a.generate as gen_a

TASK_DIR = Path(__file__).parent / "task_a" / "_run"


async def main():
    if TASK_DIR.exists():
        import shutil
        shutil.rmtree(TASK_DIR)
    print("[1/3] generating real Task A files...")
    gen_a.write_files(TASK_DIR)

    instruction = (Path(__file__).parent / "task_a" / "instruction.md").read_text()

    print("\n[2/3] solving Task A with the LLM, iterate-until-success (max 5 turns)...")
    result = await run_until_success(
        TASK_DIR, instruction, "transactions.csv", call_llm, max_turns=5,
    )

    print(f"\n[3/3] RESULT: {'PASSED' if result.passed else 'FAILED'} "
          f"in {result.turns_used} turn(s), {result.total_tokens} total tokens "
          f"({result.total_prompt_tokens} prompt + {result.total_completion_tokens} completion)")

    if not result.passed:
        print("\nTask A was NOT solved -- cannot build a real trajectory hint from a failure. "
              "Consider raising max_turns, or check turn_log below for what kept going wrong.")
        for t in result.turn_log:
            print(f"  turn {t['turn']}: {t['outcome']} -- {t['detail'][:150]}")
        return 1

    (Path(__file__).parent / "task_a_winning_solution.py").write_text(result.final_code)
    (Path(__file__).parent / "task_a_solve_log.json").write_text(json.dumps({
        "passed": result.passed, "turns_used": result.turns_used,
        "total_prompt_tokens": result.total_prompt_tokens,
        "total_completion_tokens": result.total_completion_tokens,
        "turn_log": result.turn_log,
    }, indent=2))
    print(f"\nWinning solution saved to task_a_winning_solution.py")
    print(f"Full solve log saved to task_a_solve_log.json")
    print("\nThis is now ready to use as Task B's real trajectory hint.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
