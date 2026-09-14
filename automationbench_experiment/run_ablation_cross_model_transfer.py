"""
Cross-model transfer (Part 28's shape, small/fast version): does the REAL
procedure extracted from mercury-2's run (P-0001.md, written by
run_ablation_fresh_vs_stealth.py) help a genuinely WEAKER student model
(gemma-4-31B-it, 31B, via General Compute) succeed on a sibling task --
where, unlike mercury-2 (which already saturates the `simple` domain), a
capability gap might actually exist for the injected knowledge to close?

Run D (STUDENT FRESH):   gemma-4-31B-it on task_3, no help.
Run E (STUDENT STEALTH): gemma-4-31B-it on task_3, + the SAME real
                          procedure mercury-2's trajectory produced.

Same task_3 for both (paired comparison) -- each session is independently
fresh (no conversation carryover), so this is a standard, valid AB design,
not a leak. Requires .stealth/procedures/P-0001.md to already exist (run
run_ablation_fresh_vs_stealth.py first).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from run_ablation_fresh_vs_stealth import run_one, STEALTH_DIR

STUDENT_MODEL = "gemma-4-31B-it"
STUDENT_API = "chat_completions"
STUDENT_BASE_URL = "https://api.generalcompute.com/v1"
STUDENT_API_KEY_VAR = "GENERAL_COMPUTE_API_KEY"

TASK_3 = "simple.email_sf_contact_assistant_update"


def load_stealth_context() -> str:
    path = STEALTH_DIR / "P-0001.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found -- run run_ablation_fresh_vs_stealth.py first "
            "to produce a real extracted procedure."
        )
    text = path.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1].strip()
    return "RETRIEVED PROCEDURE (from a prior agent's real, verified run on a sibling task):\n" + body


async def main():
    stealth_context = load_stealth_context()

    print(f"=== Run D: STUDENT FRESH -- {STUDENT_MODEL} on {TASK_3} ===")
    run_d = await run_one(
        model=STUDENT_MODEL, api=STUDENT_API, task_name=TASK_3, domain="simple",
        export_name="ablation_D_student_fresh.json",
        base_url=STUDENT_BASE_URL, api_key_var=STUDENT_API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_d.items() if k != "messages"}, indent=2))

    print(f"\n=== Run E: STUDENT STEALTH -- {STUDENT_MODEL} on {TASK_3} (+ P-0001.md) ===")
    run_e = await run_one(
        model=STUDENT_MODEL, api=STUDENT_API, task_name=TASK_3, domain="simple",
        export_name="ablation_E_student_stealth.json", system_prompt_suffix=stealth_context,
        base_url=STUDENT_BASE_URL, api_key_var=STUDENT_API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_e.items() if k != "messages"}, indent=2))

    print("\n=== Cross-model transfer comparison ===")
    print(json.dumps({
        "student_fresh": {"passed": run_d["passed"], "score": run_d["score"],
                           "tokens": run_d["input_tokens"] + run_d["output_tokens"],
                           "tool_calls": run_d["num_tool_calls"]},
        "student_stealth": {"passed": run_e["passed"], "score": run_e["score"],
                             "tokens": run_e["input_tokens"] + run_e["output_tokens"],
                             "tool_calls": run_e["num_tool_calls"]},
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
