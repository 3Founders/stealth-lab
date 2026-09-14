"""
The real, minimal version: the LLM is a plain execution bot. Per step, it
sees ONLY the current tasknode.md line (not the whole checklist, not a big
system-prompt blob) plus the two real tools. It executes exactly that one
step, then the driver (not the model) advances the checkbox and moves on.

Reuses AutomationBench's REAL tools (api_search/api_fetch, both plain
functions taking an explicit `world: WorldState`) and REAL grader
(partial_credit/task_completed_correctly from automationbench.rubric) --
nothing about tool execution or scoring is reimplemented or modified.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutomationBench"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

from automationbench.domains import get_combined_dataset
from automationbench.rubric import partial_credit, task_completed_correctly
from automationbench.schema.world import WorldState
from automationbench.tools.api import api_fetch, api_search
from automationbench.runner import strip_none_values

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "api_search",
            "description": "Discover the correct API endpoint URL/params for something you want to do. Call this before api_fetch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you're trying to do, e.g. 'gmail messages list'"},
                    "top_k": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "api_fetch",
            "description": "Call a real API endpoint (from a prior api_search result) to read or mutate state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "GET, POST, PUT, PATCH, DELETE"},
                    "url": {"type": "string", "description": "Full API URL from api_search results"},
                    "params": {"type": "string", "description": "Query params as a JSON string, or null"},
                    "body": {"type": "string", "description": "Request body as a JSON string, or null"},
                },
                "required": ["method", "url"],
            },
        },
    },
]

EXECBOT_SYSTEM_PROMPT = (
    "You are an execution bot. You will be given exactly ONE step to perform, taken from a "
    "checklist produced by a prior real run on this same kind of task. Do not plan ahead, do not "
    "consider other steps -- call whatever tools are needed to complete THIS ONE STEP ONLY, then "
    "reply with the single word DONE (no tool call) once it's satisfied. If the step needs no tool "
    "call (e.g. it's a filtering/decision step you can do from information already visible), just "
    "reply DONE immediately with a one-line note of what you decided."
)


def load_task(domain: str, task_name: str) -> dict:
    ds = get_combined_dataset([domain])
    short = task_name.split(".", 1)[-1]
    for row in ds:
        info = row["info"] if isinstance(row["info"], dict) else json.loads(row["info"])
        if info.get("task_name") in (task_name, short):
            return {"trigger": row["prompt"], "info": info}
    raise ValueError(f"task {task_name!r} not found in domain {domain!r}")


def parse_tasknode(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l for l in lines if re.match(r"^\d+\.\s*\[ \]", l)]


def _step_text(line: str) -> str:
    return re.sub(r"^\d+\.\s*\[ \]\s*", "", line).strip()


def mark_step_done(tasknode_path: Path, step_number: int) -> None:
    lines = tasknode_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{step_number}\.\s*\[ \]")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = pattern.sub(f"{step_number}. [x]", line)
            found = True
            break
    if not found:
        raise ValueError(f"Unchecked step {step_number} not found in {tasknode_path}")
    tasknode_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_step(client, model: str, world: WorldState, step_text: str, facts: list[str] = None, max_tool_turns: int = 4) -> tuple[list[dict], list[str]]:
    """Runs one bounded sub-loop for ONE tasknode.md line. Returns the turn log
    and a list of new facts discovered during this step."""
    fact_context = "\nFacts discovered so far:\n- " + "\n- ".join(facts) if facts else ""
    user_content = f"{fact_context}\n\nCurrent step: {step_text}".strip()
    
    messages = [
        {"role": "system", "content": EXECBOT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    log = []
    new_facts = []
    for _ in range(max_tool_turns):
        resp = await client.chat.completions.create(
            model=model, messages=messages, tools=TOOL_SCHEMAS, max_tokens=800,
        )
        msg = resp.choices[0].message
        usage = resp.usage
        log.append({
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "tool_calls": len(msg.tool_calls or []),
        })
        if not msg.tool_calls:
            break
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            try:
                if tc.function.name == "api_search":
                    result = api_search(query=args.get("query", ""), top_k=args.get("top_k", 5))
                elif tc.function.name == "api_fetch":
                    result = api_fetch(
                        world=world, method=args.get("method", "GET"), url=args.get("url", ""),
                        params=args.get("params"), body=args.get("body"),
                    )
                else:
                    result = json.dumps({"error": f"unknown tool {tc.function.name}"})
            except Exception as exc:
                result = json.dumps({"error": str(exc)})
            
            # Extract facts from tool results (simple heuristic: if it's a dict/list, stringify it)
            # In a real scenario, this could be a separate LLM call or regex, but here we capture the raw result
            new_facts.append(f"Tool {tc.function.name} returned: {str(result)[:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return log, new_facts


async def run_tasknode_driver(*, model: str, base_url: str, api_key_env: str,
                               domain: str, task_name: str, tasknode_path: Path) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ[api_key_env], base_url=base_url)
    task = load_task(domain, task_name)
    initial_state = strip_none_values(task["info"].get("initial_state", {}))
    world = WorldState(**initial_state)

    steps = parse_tasknode(tasknode_path)
    turn_log: list[dict] = []
    accumulated_facts: list[str] = []
    
    for i, line in enumerate(steps, 1):
        step_log, step_facts = await run_step(client, model, world, _step_text(line), facts=accumulated_facts)
        turn_log.extend(step_log)
        accumulated_facts.extend(step_facts)
        mark_step_done(tasknode_path, i)

    state: dict[str, Any] = {"world": world, "info": task["info"]}
    score = partial_credit(state)
    passed = task_completed_correctly(state)

    return {
        "task": task_name, "passed": bool(passed), "score": score,
        "input_tokens": sum(t["input_tokens"] for t in turn_log),
        "output_tokens": sum(t["output_tokens"] for t in turn_log),
        "num_tool_calls": sum(t["tool_calls"] for t in turn_log),
        "num_steps_executed": len(steps),
    }
