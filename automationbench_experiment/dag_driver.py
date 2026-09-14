"""
The real, integrated loop: local-first DAG execution.

Per node, in order:
  1. Zero-LLM-cost check: does `.stealth/implementations/` already have a
     clean (100% success) recorded tool-call sequence for this EXACT goal
     text? If so, replay it directly against `world` -- no model call at
     all for this node.
  2. Otherwise, a real bounded SLM turn (facts-carrying, per the earlier
     fix) executes the step, and the resulting tool-call sequence is
     recorded (success or failure) for next time.

Live state (`.stealth/run.md`) is read/written directly, no Postgres
round-trips per step. Postgres only gets touched by a separate, later
flush step once the whole run is complete (not built here yet -- this
module is steps 1+2 of the agreed 3-step plan).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutomationBench"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

from automationbench.rubric import partial_credit, task_completed_correctly
from automationbench.schema.world import WorldState
from automationbench.tools.api import api_fetch, api_search

from local_dag_state import DagNode, create_run, is_complete, mark_node, next_actionable_node
from local_implementations import record_local_implementation, resolve_local_implementation

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "api_search",
            "description": "Discover the correct API endpoint URL/params for something you want to do. Call this before api_fetch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
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
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "params": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["method", "url"],
            },
        },
    },
]

EXECBOT_SYSTEM_PROMPT = (
    "You are an execution bot. You will be given exactly ONE step to perform. Do not plan ahead -- "
    "call whatever tools are needed to complete THIS ONE STEP ONLY, then reply DONE (no tool call) "
    "once satisfied, or reply DONE with a one-line note if the step needs no tool call."
)


def _dispatch_tool_call(world: WorldState, name: str, args: dict) -> Any:
    if name == "api_search":
        return api_search(query=args.get("query", ""), top_k=args.get("top_k", 5))
    if name == "api_fetch":
        return api_fetch(
            world=world, method=args.get("method", "GET"), url=args.get("url", ""),
            params=args.get("params"), body=args.get("body"),
        )
    return json.dumps({"error": f"unknown tool {name}"})


def _replay(world: WorldState, tool_calls: list[dict]) -> None:
    """Zero-LLM-cost path: apply a previously-proven tool-call sequence directly."""
    for tc in tool_calls:
        _dispatch_tool_call(world, tc["name"], tc.get("arguments", {}))


async def _execute_via_llm(client, model: str, world: WorldState, goal: str, facts: list[str],
                            max_tool_turns: int = 4) -> tuple[bool, list[dict], list[str], dict]:
    """Returns (succeeded, tool_calls_made, new_facts, token_usage)."""
    fact_block = ("Facts discovered so far:\n" + "\n".join(f"- {f}" for f in facts) + "\n\n") if facts else ""
    messages = [
        {"role": "system", "content": EXECBOT_SYSTEM_PROMPT},
        {"role": "user", "content": f"{fact_block}Current step: {goal}"},
    ]
    tool_calls_made: list[dict] = []
    new_facts: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "num_calls": 0}
    succeeded = True

    for _ in range(max_tool_turns):
        resp = await client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS, max_tokens=800)
        usage["num_calls"] += 1
        if resp.usage:
            usage["input_tokens"] += resp.usage.prompt_tokens
            usage["output_tokens"] += resp.usage.completion_tokens
        msg = resp.choices[0].message
        if not msg.tool_calls:
            break
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            try:
                result = _dispatch_tool_call(world, tc.function.name, args)
                new_facts.append(f"{tc.function.name} -> {str(result)[:200]}")
            except Exception as exc:  # noqa: BLE001 -- a bad call becomes a tool-error message, not a crash
                result = json.dumps({"error": str(exc)})
                succeeded = False
            tool_calls_made.append({"name": tc.function.name, "arguments": args})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return succeeded, tool_calls_made, new_facts, usage


async def run_dag(*, model: str, base_url: str, api_key_env: str, world: WorldState,
                   run_md_path: Path, implementations_dir: Path, info: dict,
                   max_tool_turns: int = 4) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ[api_key_env], base_url=base_url)
    facts: list[str] = []
    totals = {"input_tokens": 0, "output_tokens": 0, "num_llm_calls": 0, "num_nodes_replayed": 0, "num_nodes_via_llm": 0}
    # Buffered, NOT written to the cache yet: whether a node's own tool calls ran
    # cleanly says nothing about whether the run as a WHOLE actually passed the
    # real grader. Only a run that passes end-to-end gets its nodes recorded as
    # reusable implementations -- a node from a failing run must not be cached
    # as "proven" just because it happened to throw no exceptions.
    pending_records: list[tuple[str, list[dict]]] = []

    while not is_complete(run_md_path):
        node = next_actionable_node(run_md_path)
        if node is None:
            break  # nothing actionable and not complete -> blocked on a failed dep; stop honestly

        cached = resolve_local_implementation(implementations_dir, node.goal)
        if cached is not None:
            _replay(world, cached.tool_calls)
            mark_node(run_md_path, node.order, "succeeded")
            totals["num_nodes_replayed"] += 1
            continue

        succeeded, tool_calls_made, new_facts, usage = await _execute_via_llm(
            client, model, world, node.goal, facts, max_tool_turns=max_tool_turns,
        )
        if tool_calls_made:
            pending_records.append((node.goal, tool_calls_made))
        mark_node(run_md_path, node.order, "succeeded" if succeeded else "failed")
        facts.extend(new_facts)
        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
        totals["num_llm_calls"] += usage["num_calls"]
        totals["num_nodes_via_llm"] += 1

    state: dict[str, Any] = {"world": world, "info": info}
    # task_completed_correctly reads state["partial_credit"], cached by the
    # call below -- partial_credit MUST run first or it always reads the
    # default 0.0 and reports every run as failed.
    score = partial_credit(state)
    task_passed = bool(task_completed_correctly(state))

    # Flush the buffer now that the real, whole-task outcome is known. Every
    # buffered node gets the SAME success flag -- the task's own pass/fail --
    # not each node's own no-exceptions flag from above.
    for goal, tool_calls_made in pending_records:
        record_local_implementation(implementations_dir, goal, tool_calls=tool_calls_made, success=task_passed)

    return {
        "score": score, "passed": task_passed,
        **totals,
    }
