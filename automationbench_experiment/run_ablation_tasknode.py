"""
Redesigned Stealth condition per direct feedback: the injected knowledge
should not be a rationale-heavy paragraph the model has to read and
reason about every turn -- it should be a literal state file (tasknode.md)
that turns the model into a plain EXECUTION BOT: read the next unexecuted
line, do exactly that, mark it done, move to the next line. No re-planning,
no re-deriving strategy -- the plan already happened (in a prior real run);
this run just executes it.

Source trajectory: support.zendesk_sf_case_sync, minimax-m2.7, real score
0.8125 (44 tool calls, 15 turns) -- the best-scoring real run found across
all support-domain attempts. Not a full pass (no clean pass exists yet for
any model on this task), so the extracted checklist is tagged best-effort/
unverified, matching this codebase's own honest epistemic-status
convention -- never presented as ground truth.

Stage A (FRESH):    minimax-m2.7 on TASK (no help).
Stage B (EXTRACT):  a real LLM call reads the real 44-tool-call trajectory
                    and distills a literal, numbered action checklist
                    (imperative steps only, no rationale prose) -- written
                    to tasknode.md.
Stage C (TASKNODE):  minimax-m2.7 on TASK again, system prompt replaced
                    with a short execution-bot directive + tasknode.md's
                    literal content (not the old rationale-style inject).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from run_single_task import run_one, RESULTS_DIR

TASK = "support.zendesk_sf_case_sync"
MODEL = "minimax-m2.7"
API = "chat_completions"
BASE_URL = "https://api.generalcompute.com/v1"
API_KEY_VAR = "GENERAL_COMPUTE_API_KEY"
EXTRACTOR_MODEL = "gemma-4-31B-it"

TASKNODE_PATH = Path(__file__).parent / "tasknode.md"

SOURCE_RESULTS_FILE = RESULTS_DIR / "model_compare_minimax-m2.7_support3.json"

EXTRACTION_SYSTEM_PROMPT = """You read a real agent trajectory (tool calls + real tool outputs) \
that scored 0.8125/1.0 (not a full pass, but the closest real attempt found) on a business-workflow \
task. Extract a literal, numbered CHECKLIST of concrete actions -- imperative, one tool-call's worth \
of work per line, in the order they should happen. No rationale, no "why", no prose paragraphs -- \
just the bare sequence of what to do, generalized enough to survive different specific record values \
but concrete enough that another agent could execute each line as a single tool action.

Respond with ONLY a JSON object: {"checklist": [str, ...]}
If nothing generalizable exists, respond with exactly: NONE
"""


async def extract_checklist_from_trajectory(messages: list) -> list[str] | None:
    from openai import AsyncOpenAI
    import os

    client = AsyncOpenAI(api_key=os.environ["GENERAL_COMPUTE_API_KEY"], base_url=BASE_URL)
    trace_text = json.dumps(messages, default=str)[:10000]
    resp = await client.chat.completions.create(
        model=EXTRACTOR_MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": trace_text},
        ],
        temperature=0.0, max_tokens=700,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text == "NONE" or not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1]).get("checklist")
    except json.JSONDecodeError:
        return None


def write_tasknode(checklist: list[str]) -> str:
    lines = "\n".join(f"{i+1}. [ ] {step}" for i, step in enumerate(checklist))
    content = f"# tasknode\n\n{lines}\n"
    TASKNODE_PATH.write_text(content, encoding="utf-8")
    return content


EXECUTION_BOT_PROMPT_PREFIX = (
    "You are an execution bot. tasknode.md below is your ONLY plan -- it was produced from a "
    "prior real run on this same kind of task. Do not re-plan or second-guess it. Read the first "
    "unchecked ([ ]) line, execute exactly that one action with your tools, then move to the next "
    "unchecked line. Continue until every line is done or the task is complete.\n\n"
    "tasknode.md:\n"
)


async def main():
    print(f"=== Stage A: FRESH -- {MODEL} on {TASK} ===")
    run_a = await run_one(
        model=MODEL, api=API, task_name=TASK, domain="support",
        export_name="tasknode_A_fresh.json", base_url=BASE_URL, api_key_var=API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_a.items() if k != "messages"}, indent=2))

    print("\n=== Stage B: EXTRACT literal checklist from best real trajectory (score 0.8125) ===")
    source = json.loads(SOURCE_RESULTS_FILE.read_text(encoding="utf-8"))
    source_task = [t for t in source["tasks"] if t["name"] == TASK][0]
    checklist = await extract_checklist_from_trajectory(source_task["messages"])
    if checklist is None:
        print("Extraction returned NONE. Stopping honestly.")
        return
    content = write_tasknode(checklist)
    print(f"Wrote tasknode.md ({len(checklist)} steps) -> {TASKNODE_PATH}")
    print(content)

    print(f"\n=== Stage C: TASKNODE execution-bot -- {MODEL} on {TASK} ===")
    run_c = await run_one(
        model=MODEL, api=API, task_name=TASK, domain="support",
        export_name="tasknode_C_execbot.json",
        system_prompt_suffix=EXECUTION_BOT_PROMPT_PREFIX + content,
        base_url=BASE_URL, api_key_var=API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_c.items() if k != "messages"}, indent=2))

    print("\n=== Comparison ===")
    print(json.dumps({
        "fresh": {"passed": run_a["passed"], "score": run_a["score"],
                  "tokens": run_a["input_tokens"] + run_a["output_tokens"],
                  "tool_calls": run_a["num_tool_calls"]},
        "tasknode_execbot": {"passed": run_c["passed"], "score": run_c["score"],
                              "tokens": run_c["input_tokens"] + run_c["output_tokens"],
                              "tool_calls": run_c["num_tool_calls"]},
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
