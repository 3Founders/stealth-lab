"""
The real full-system pipeline (retrieval -> SLM execution -> cost
breakdown), parameterized by task so Task B and Task C (and any future
task) share this logic instead of duplicating it -- extracted once
this became the third real use of the same shape.
"""
import json
import shutil
from pathlib import Path

from experiment_4_common import call_slm
from iterate import run_until_success
from retrieve_trajectory import retrieve_for_instruction

# Real, current pricing -- see TECHNICAL_DEEP_DIVE.md for sourcing.
# Groq qwen/qwen3.6-27b: confirmed via multiple independent sources.
SLM_INPUT_PRICE_PER_M = 0.60
SLM_OUTPUT_PRICE_PER_M = 3.00
# Voyage embedding price -- confirmed real via direct search
# (multiple consistent sources), not a placeholder guess.
EMBED_PRICE_PER_M = 0.18  # voyage-3-large, confirmed real current list price


async def run_full_system_core(
    task_label: str, task_dir_name: str, csv_filename: str, gen_module, here: Path,
    retrieval_threshold: float | None = None, run_suffix: str = "",
) -> dict:
    """
    Real core logic, returns the full stats dict -- used directly by
    repeat-runners that need real per-run data, not just an exit code.

    run_suffix: appended to output filenames (e.g. "_rep1") so repeated
    calls in a loop don't overwrite each other's results/winning code.
    Empty by default, preserving the original single-run filenames for
    the existing Task B/C CLI scripts.
    """
    print("=" * 70)
    print(f"STEP 1: real retrieval against the real ingested trajectory library ({task_label}{run_suffix})")
    print("=" * 70)
    instruction = (here / task_dir_name / "instruction.md").read_text()
    content, node_id = await retrieve_for_instruction(instruction, threshold=retrieval_threshold)
    if content is None:
        print("\nCannot proceed -- retrieval found nothing usable. Run "
              "ingest_trajectory_library.py first if you haven't.")
        return {"task": task_label, "passed": False, "error": "retrieval_found_nothing"}

    approx_embed_tokens = len(instruction) // 4  # rough chars/4 estimate, NOT exact -- flagged
    embed_cost = approx_embed_tokens / 1_000_000 * EMBED_PRICE_PER_M

    print("\n" + "=" * 70)
    print(f"STEP 2: real SLM execution using the RETRIEVED trajectory ({task_label}{run_suffix})")
    print("=" * 70)
    task_dir = here / task_dir_name / f"full_system_run{run_suffix}"
    shutil.rmtree(task_dir, ignore_errors=True)
    gen_module.write_files(task_dir)

    hinted_instruction = content + "\n---\n\n" + instruction
    result = await run_until_success(
        task_dir, hinted_instruction, csv_filename, call_slm, max_turns=5,
    )

    generation_cost = (
        result.total_prompt_tokens / 1_000_000 * SLM_INPUT_PRICE_PER_M
        + result.total_completion_tokens / 1_000_000 * SLM_OUTPUT_PRICE_PER_M
    )
    total_cost = embed_cost + generation_cost

    print("\n" + "=" * 70)
    print(f"FULL SYSTEM COST BREAKDOWN ({task_label}{run_suffix})")
    print("=" * 70)
    print(f"Result: {'PASSED' if result.passed else 'FAILED'} in {result.turns_used} turn(s)")
    print(f"Retrieval: ~{approx_embed_tokens} tokens (rough estimate), ~${embed_cost:.6f}")
    print(f"Generation: {result.total_prompt_tokens} prompt + {result.total_completion_tokens} "
          f"completion tokens, ${generation_cost:.6f}")
    print(f"TOTAL real full-system cost for this solve: ${total_cost:.6f}")

    out = {
        "task": task_label, "passed": result.passed, "turns_used": result.turns_used,
        "retrieval_threshold_used": retrieval_threshold if retrieval_threshold is not None else "default (0.70)",
        "retrieved_node_id": node_id,
        "approx_embed_tokens": approx_embed_tokens, "embed_cost_usd": embed_cost,
        "prompt_tokens": result.total_prompt_tokens,
        "completion_tokens": result.total_completion_tokens,
        "generation_cost_usd": generation_cost,
        "total_cost_usd": total_cost,
    }
    (here / f"full_system_results_{task_dir_name}{run_suffix}.json").write_text(json.dumps(out, indent=2))
    if result.passed:
        (here / f"{task_dir_name}_winning_solution{run_suffix}.py").write_text(result.final_code)
        print(f"winning code saved to {here / f'{task_dir_name}_winning_solution{run_suffix}.py'}")
    print(f"\nfull results saved to {here / f'full_system_results_{task_dir_name}{run_suffix}.json'}")
    return out


async def run_full_system(
    task_label: str, task_dir_name: str, csv_filename: str, gen_module, here: Path,
    retrieval_threshold: float | None = None,
) -> int:
    """Thin CLI-friendly wrapper over run_full_system_core -- unchanged
    behavior/signature for the existing single-run Task B/C scripts."""
    out = await run_full_system_core(
        task_label, task_dir_name, csv_filename, gen_module, here, retrieval_threshold,
    )
    return 0 if out.get("passed") else 1
