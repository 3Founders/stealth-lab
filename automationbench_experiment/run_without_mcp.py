"""
WITHOUT MCP: same task, same model, plain one-shot request -- no retrieval,
no MCP tools, no DAG. The model is given the current file content and asked
to write the fix as a unified diff / full file. Applied and tested by hand.
"""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

from openai import AsyncOpenAI

TASK_DESCRIPTION = """In tasknode_driver.py, fix two real bugs found during live testing:

1. The driver never persists step completion back to tasknode.md. Add a function `mark_step_done(tasknode_path: Path, step_number: int) -> None` that reads the file, changes the line starting with f"{step_number}. [ ]" to f"{step_number}. [x]" (preserving the rest of the line's text unchanged), and writes the file back. Raise ValueError if that step_number's unchecked line isn't found. Wire `run_tasknode_driver` to call this after each step in its loop completes.

2. Each step currently gets a completely fresh mini-conversation with zero memory of facts discovered in earlier steps (e.g. step 2 discovers "new phone = +1-555-0199" but step 4, which needs to use that value, never sees it -- this caused a real observed task failure, score 0.0, in an otherwise-working task). Fix this by having `run_step` return any concrete facts discovered (parse them from the tool results it received -- e.g. extracted field values, record IDs) as a list of short strings, and have `run_tasknode_driver` accumulate these into a running `facts: list[str]` that gets prepended to the NEXT step's prompt as a short "Facts discovered so far:" block (not the whole prior conversation, just this compact list) so later steps have what they need without re-adding the full context overhead the earlier ablations already showed doesn't help.

Add tests in a new tests/test_tasknode_driver.py covering: mark_step_done changes exactly the right line and preserves others; mark_step_done raises ValueError for an already-checked or nonexistent step_number; and the facts-accumulation logic (test the pure logic, not a live LLM call -- use a fake/stub if the real function needs one).

Respond with ONLY the two full updated files, each preceded by a line "=== FILE: <path> ===" (paths: "tasknode_driver.py" and "tests/test_tasknode_driver.py"). No explanation, no markdown fences, just the two files' full content."""


async def main():
    client = AsyncOpenAI(
        api_key=os.environ["GENERAL_COMPUTE_API_KEY"], base_url="https://api.generalcompute.com/v1"
    )
    current_content = Path(__file__).parent.joinpath("tasknode_driver.py").read_text(encoding="utf-8")
    user_prompt = f"Current file content of tasknode_driver.py:\n\n{current_content}\n\n---\n\nTask:\n{TASK_DESCRIPTION}"

    resp = await client.chat.completions.create(
        model="gemma-4-31B-it",
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4000,
        temperature=0.0,
    )
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    print(json.dumps({
        "input_tokens": usage.prompt_tokens if usage else None,
        "output_tokens": usage.completion_tokens if usage else None,
        "num_calls": 1,
        "num_tool_calls": 0,
    }, indent=2))
    Path(__file__).parent.joinpath("without_mcp_raw_response.txt").write_text(text, encoding="utf-8")
    print("Raw response saved to without_mcp_raw_response.txt")


if __name__ == "__main__":
    asyncio.run(main())
