"""
The actual full-system test: real retrieval (find_reusable_nodes
against the real ingested library) -> real SLM execution
(iterate-until-success) -> full cost broken out by component
(embedding vs. generation), not lumped together.

Run from backend/ (after ingest_trajectory_library.py):
    python scripts/synthetic_tasks/run_task_b_full_system.py
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

from experiment_4_common import call_slm
from iterate import run_until_success
from retrieve_trajectory import retrieve_for_task_b
import task_b.generate as gen_b

HERE = Path(__file__).parent

# Real, current pricing -- see TECHNICAL_DEEP_DIVE.md for sourcing.
# Groq qwen/qwen3.6-27b: confirmed via multiple independent sources.
SLM_INPUT_PRICE_PER_M = 0.60
SLM_OUTPUT_PRICE_PER_M = 3.00
# Voyage embedding price -- confirmed real via direct search
# (multiple consistent sources), NOT the earlier placeholder guess.
EMBED_PRICE_PER_M = 0.18  # voyage-3-large, confirmed real current list price


async def main():
    print("=" * 70)
    print("STEP 1: real retrieval against the real ingested trajectory library")
    print("=" * 70)
    content, node_id = await retrieve_for_task_b()
    if content is None:
        print("\nCannot proceed -- retrieval found nothing usable. Run "
              "ingest_trajectory_library.py first if you haven't.")
        return 1

    # Rough embedding cost estimate -- Task B's instruction.md is the only
    # text embedded this run (the library was embedded once, already, at
    # ingestion time, a sunk cost not repeated per-query).
    instruction = (HERE / "task_b" / "instruction.md").read_text()
    approx_embed_tokens = len(instruction) // 4  # rough chars/4 estimate, NOT exact -- flagged
    embed_cost = approx_embed_tokens / 1_000_000 * EMBED_PRICE_PER_M

    print("\n" + "=" * 70)
    print("STEP 2: real SLM execution using the RETRIEVED trajectory")
    print("=" * 70)
    task_dir = HERE / "task_b" / "full_system_run"
    shutil.rmtree(task_dir, ignore_errors=True)
    gen_b.write_files(task_dir)

    hinted_instruction = content + "\n---\n\n" + instruction
    result = await run_until_success(
        task_dir, hinted_instruction, "activity_logs.csv", call_slm, max_turns=5,
    )

    generation_cost = (
        result.total_prompt_tokens / 1_000_000 * SLM_INPUT_PRICE_PER_M
        + result.total_completion_tokens / 1_000_000 * SLM_OUTPUT_PRICE_PER_M
    )
    total_cost = embed_cost + generation_cost

    print("\n" + "=" * 70)
    print("FULL SYSTEM COST BREAKDOWN")
    print("=" * 70)
    print(f"Result: {'PASSED' if result.passed else 'FAILED'} in {result.turns_used} turn(s)")
    print(f"Retrieval: ~{approx_embed_tokens} tokens (rough estimate), ~${embed_cost:.6f}")
    print(f"Generation: {result.total_prompt_tokens} prompt + {result.total_completion_tokens} "
          f"completion tokens, ${generation_cost:.6f}")
    print(f"TOTAL real full-system cost for this solve: ${total_cost:.6f}")

    out = {
        "passed": result.passed, "turns_used": result.turns_used,
        "retrieved_node_id": node_id,
        "approx_embed_tokens": approx_embed_tokens, "embed_cost_usd": embed_cost,
        "prompt_tokens": result.total_prompt_tokens,
        "completion_tokens": result.total_completion_tokens,
        "generation_cost_usd": generation_cost,
        "total_cost_usd": total_cost,
    }
    (HERE / "full_system_results.json").write_text(json.dumps(out, indent=2))
    print(f"\nfull results saved to {HERE / 'full_system_results.json'}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
