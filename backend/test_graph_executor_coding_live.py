"""
Real, live test of graph_executor.py against a genuine small coding task --
not arithmetic. Minimal interface, on purpose: run_node does nothing but
construct the SAME Agent + RepoSandbox find_best_way's tier-2 already uses
and call .run() for real, once per node, against one shared real repo
directory so each node's edits are visible to the next. No new machinery.

Real repo: /tmp/graph_exec_demo/calc.py, seeded with a real bug
(`return a - b` where it should be `+`).

Three real, dependent coding steps:
  0. locate the bug (read the file, report what's wrong) via a real
     tool-calling agent loop, ending in `finish`
  1. depends on 0: fix it for real (a real edit_file call)
  2. depends on 1: verify the fix landed by reading the file back

Hand-run, not part of pytest (registered in test_live_scripts_not_collected.py).
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "experiments", "swebench_pro"))

from openai import OpenAI
from agent import Agent, RepoSandbox

from app.execution.graph_executor import NodeResult, execute_task_graph
from app.models.plan import PlanNode, TaskGraph
from app.config import settings
from uuid import uuid4

import tempfile
REPO_PATH = os.path.join(tempfile.gettempdir(), "graph_exec_demo")

NODES = [
    PlanNode(order=0, goal="Read calc.py, then call the finish tool with a one-sentence summary of exactly what is wrong with the add() function. Do not fix it yet.", deps=[]),
    PlanNode(order=1, goal="Fix the bug in calc.py's add() function so it actually adds the two numbers instead of subtracting them, then call finish.", deps=[0]),
    PlanNode(order=2, goal="Read calc.py again, then call finish with a summary confirming whether add() now returns a + b instead of a - b.", deps=[1]),
]

client = OpenAI(
    max_retries=0,
    api_key=settings.require("general_compute_api_key"),
    base_url=settings.general_compute_base_url,
)


async def run_node(node: PlanNode) -> NodeResult:
    print(f"\n--- REAL AGENT RUN for node {node.order}: {node.goal!r}")
    agent = Agent(client, "gemma-4-31B-it", max_steps=6)
    sandbox = RepoSandbox(REPO_PATH)  # same real directory every node -- edits persist
    instance = {
        "instance_id": f"graph_exec_demo_node_{node.order}",
        "repo": "graph_exec_demo",
        "problem_statement": node.goal,
    }
    run_result = await asyncio.to_thread(agent.run, instance, sandbox, "graph_exec_demo", "")

    print(f"    stop_reason={run_result.stop_reason}  tool_calls={run_result.tool_calls}  "
          f"files_edited={run_result.files_edited}")
    if run_result.patch:
        print(f"    real diff:\n{run_result.patch}")

    succeeded = run_result.stop_reason == "finished"
    return NodeResult(
        status="success" if succeeded else "failure",
        notes=f"stop_reason={run_result.stop_reason}, tool_calls={len(run_result.tool_calls)}",
    )


async def main():
    print("BEFORE:", open(f"{REPO_PATH}/calc.py").read())

    graph = TaskGraph(execution_plan_id=uuid4(), graph_hash="live-coding-test", nodes=NODES)
    result = await execute_task_graph(graph, run_node=run_node)

    print("\n=== GRAPH EXECUTION RESULT ===")
    print("overall outcome:", result.outcome)
    for order in sorted(result.node_statuses):
        print(f"  node {order}: {result.node_statuses[order]}")

    real_content = open(f"{REPO_PATH}/calc.py").read()
    print("\nAFTER:", real_content)

    assert result.outcome == "success", f"FAIL: expected success, got {result.outcome}"
    assert "a + b" in real_content, "FAIL: the real file on disk was not actually fixed"
    assert "a - b" not in real_content, "FAIL: buggy line still present on disk"
    print("\nPASS: three real, dependent coding steps executed in order through "
          "graph_executor, each a genuine tool-calling Agent run against a real "
          "repo on disk -- the bug is actually fixed, verified on disk, not simulated.")


if __name__ == "__main__":
    asyncio.run(main())
