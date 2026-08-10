"""
Runs the real 2x2 on Task B: {SLM, LLM} x {no hint, real trajectory
hint}. Uses the iterate-until-success loop for every cell (not
single-shot) -- the fair, agent-realistic harness this whole design
converged on.

Hint format: a MASKED TaskNode trajectory (typed slots, an ordered
step chain, explicit postconditions), NOT Task A's raw pasted source
code. Switched from raw code after a real gap was found: pasting
working code let the model nearly copy-adapt it mechanically, which
tests a materially easier and different thing than what the actual
architecture does (retrieval of a masked schema + step chain,
requiring genuine slot-filling). See masked_trajectory.py for the
full reasoning and the hand-authored trajectory itself, built from
Task A's real, actually-solved logic.

Exposes run_2x2_once() as an importable function so
run_task_b_repeated.py can call it N times without duplicating this
file's logic.

Run from backend/:
    python scripts/synthetic_tasks/run_task_b_2x2.py
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from experiment_4_common import call_llm, call_slm
from iterate import run_until_success
from masked_trajectory import TASK_A_MASKED_TRAJECTORY
import task_b.generate as gen_b

HERE = Path(__file__).parent


async def run_one_cell(label: str, task_dir: Path, instruction: str, call_model_fn) -> dict:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    result = await run_until_success(
        task_dir, instruction, "activity_logs.csv", call_model_fn, max_turns=5,
    )
    print(f"  -> {'PASSED' if result.passed else 'FAILED'} in {result.turns_used} turn(s), "
          f"{result.total_tokens} tokens ({result.total_prompt_tokens}+{result.total_completion_tokens})")
    return {
        "label": label, "passed": result.passed, "turns_used": result.turns_used,
        "total_tokens": result.total_tokens,
        "total_prompt_tokens": result.total_prompt_tokens,
        "total_completion_tokens": result.total_completion_tokens,
        # Saved so repeated runs can check for suspiciously-identical
        # output across repetitions (e.g. provider-side caching of
        # identical prompts) rather than assuming independent calls.
        "final_code": result.final_code,
    }


async def run_2x2_once(run_id: str = "") -> list[dict]:
    """Runs all 4 cells once, returns their results. run_id disambiguates
    task directories across repeated calls (run_task_b_repeated.py)."""
    base_instruction = (HERE / "task_b" / "instruction.md").read_text()
    hinted_instruction = TASK_A_MASKED_TRAJECTORY + "\n---\n\n" + base_instruction

    results = []
    cells = [
        ("Cell 1: SLM, no hint", call_slm, base_instruction, f"task_b_run1{run_id}"),
        ("Cell 2: SLM, with masked trajectory hint", call_slm, hinted_instruction, f"task_b_run2{run_id}"),
        ("Cell 3: LLM, no hint", call_llm, base_instruction, f"task_b_run3{run_id}"),
        ("Cell 4: LLM, with masked trajectory hint", call_llm, hinted_instruction, f"task_b_run4{run_id}"),
    ]

    for label, call_fn, instruction, dirname in cells:
        task_dir = HERE / "task_b" / dirname
        shutil.rmtree(task_dir, ignore_errors=True)
        gen_b.write_files(task_dir)
        result = await run_one_cell(label, task_dir, instruction, call_fn)
        results.append(result)

    return results


async def main():
    results = await run_2x2_once()

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    print(f"{'Cell':<45} {'Result':<8} {'Turns':<7} {'Tokens':<8} {'CompletionTok':<14}")
    for r in results:
        print(f"{r['label']:<45} {'PASS' if r['passed'] else 'FAIL':<8} "
              f"{r['turns_used']:<7} {r['total_tokens']:<8} {r['total_completion_tokens']:<14}")

    (HERE / "task_b_2x2_results.json").write_text(json.dumps(results, indent=2))
    print(f"\nfull results saved to {HERE / 'task_b_2x2_results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
