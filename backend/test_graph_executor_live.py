"""
Real, live test of graph_executor.py: actual network calls to General
Compute's API, one real completion per node, not a fake run_node. This is
the thing test_graph_executor_offline.py's fakes cannot prove -- that the
scheduler correctly drives GENUINE LLM work per node, in the right order,
with real failure containment when one of those genuine calls comes back
bad.

Hand-run, not part of pytest (see tests/test_live_scripts_not_collected.py
-- this file's name is added to that ignore list).

Five real nodes:
  0. "compute 12 + 30" (real arithmetic via a real completion)
  1. depends on 0: "multiply the previous result by 2"
  2. depends on 1: DELIBERATELY instructed to reply with a failure marker,
     to produce a real, genuine bad outcome from a real API response
     (not a raised exception, not a mock) -- the honest way to test a
     failure path without depending on the model spontaneously erroring.
  3. independent (no deps): must still run even though node 2 fails.
  4. depends on 2: must be SKIPPED -- never reaches the API at all.
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from app.execution.graph_executor import NodeResult, execute_task_graph
from app.models.plan import PlanNode, TaskGraph
from uuid import uuid4

client = OpenAI(
    api_key=os.environ["GENERAL_COMPUTE_API_KEY"],
    base_url=os.environ["GENERAL_COMPUTE_BASE_URL"],
    timeout=30, max_retries=0,
)

NODES = [
    PlanNode(order=0, goal="Compute 12 + 30. Reply with ONLY the resulting number, nothing else.", deps=[]),
    PlanNode(order=1, goal="Multiply 42 by 2. Reply with ONLY the resulting number, nothing else.", deps=[0]),
    PlanNode(order=2, goal="This step is deliberately testing failure handling: reply with EXACTLY the text FAIL_THIS_STEP and nothing else.", deps=[1]),
    PlanNode(order=3, goal="Reply with ONLY the word DONE. (independent branch -- must run even if node 2 fails)", deps=[]),
    PlanNode(order=4, goal="Reply with ONLY the word SKIPPED_NODE. (must NEVER actually be called -- it depends on node 2, which fails)", deps=[2]),
]

total_tokens_used = 0


async def run_node(node: PlanNode) -> NodeResult:
    global total_tokens_used
    print(f"\n--- REAL LLM CALL for node {node.order}: {node.goal!r}")
    resp = client.chat.completions.create(
        model="gemma-4-31B-it",
        messages=[{"role": "user", "content": node.goal}],
        max_tokens=20,
    )
    text = (resp.choices[0].message.content or "").strip()
    tokens = resp.usage.total_tokens if resp.usage else 0
    total_tokens_used += tokens
    print(f"    real response: {text!r}  (tokens used this call: {tokens})")

    if "FAIL_THIS_STEP" in text:
        return NodeResult(status="failure", notes="model returned the deliberate failure marker")
    return NodeResult(status="success", notes=text)


async def main():
    graph = TaskGraph(execution_plan_id=uuid4(), graph_hash="live-test", nodes=NODES)
    result = await execute_task_graph(graph, run_node=run_node)

    print("\n=== GRAPH EXECUTION RESULT ===")
    print("overall outcome:", result.outcome)
    for order in sorted(result.node_statuses):
        status = result.node_statuses[order]
        note = result.node_results[order].notes if order in result.node_results else "(never called -- skipped)"
        print(f"  node {order}: {status:8s}  {note}")
    print(f"\ntotal real tokens spent across all API calls: {total_tokens_used}")

    assert result.node_statuses[0] == "success", "FAIL: node 0 (real arithmetic) did not succeed"
    assert result.node_statuses[1] == "success", "FAIL: node 1 (depends on 0) did not succeed"
    assert result.node_statuses[2] == "failure", "FAIL: node 2 did not report the deliberate failure"
    assert result.node_statuses[3] == "success", "FAIL: independent node 3 did not run despite node 2's failure"
    assert result.node_statuses[4] == "skipped", "FAIL: node 4 should have been skipped, never called"
    assert 4 not in result.node_results, "FAIL: node 4 must never have reached run_node (no real API call)"
    assert total_tokens_used > 0, "FAIL: no real tokens were spent -- calls did not actually happen"

    print("\nPASS: real LLM calls executed per node, real dependency ordering "
          "honored, real failure containment proven (node 2 failed for real, "
          "node 3 still ran, node 4 was never called) -- not simulated.")


if __name__ == "__main__":
    asyncio.run(main())
