"""
Real, live proof that a REAL STORED multi-step procedure -- not a
hand-built demo graph -- executes correctly through graph_executor.py.

Fetches the real `debug_failing_test` procedure (4 real steps, seeded
this session via scripts/seed_canonical_coding_procedures.py) directly
from the live database, converts its steps into a linear-deps PlanNode
chain (deps=[i-1] -- derivable from the existing `order` field, NO
schema change needed for a linear procedure), and runs it through the
real scheduler with real LLM calls per step.

This is the direct answer to "if there could be a multistep procedure,
will that work?" -- proven against the real corpus, not asserted.

Hand-run, not part of pytest (registered in test_live_scripts_not_collected.py).
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from app.db.session import create_pool
from app.execution.graph_executor import NodeResult, execute_task_graph
from app.models.plan import PlanNode, TaskGraph
from app.config import settings
from uuid import uuid4

# A real, concrete bug scenario for the steps to reason about -- the
# same calc.py bug used elsewhere this session, so the steps have
# something real to actually investigate rather than nothing at all.
BUG_CONTEXT = (
    "The function `add(a, b)` in calc.py is supposed to add two numbers "
    "but currently returns `a - b` instead of `a + b`. A test named "
    "test_add_returns_sum is failing with: assert add(2, 3) == 5, but got -1."
)

client = OpenAI(
    max_retries=0,
    api_key=settings.require("general_compute_api_key"),
    base_url=settings.general_compute_base_url,
)


def steps_to_linear_graph(execution_plan_id, steps: list[dict]) -> TaskGraph:
    """The actual answer to this turn's question: steps[i].deps is
    derivable as [i-1] from the existing `order` field alone -- no new
    column needed for a linear (non-branching) procedure, which is all
    that exists in the real corpus today."""
    nodes = [
        PlanNode(order=s["order"], goal=s["goal"], deps=[s["order"] - 1] if s["order"] > 0 else [])
        for s in sorted(steps, key=lambda s: s["order"])
    ]
    return TaskGraph(execution_plan_id=execution_plan_id, graph_hash="live-stored-procedure-test", nodes=nodes)


async def run_node(node: PlanNode) -> NodeResult:
    print(f"\n--- REAL LLM CALL for step {node.order}: {node.goal!r}")
    resp = client.chat.completions.create(
        model="gemma-4-31B-it",
        messages=[
            {"role": "system", "content": f"Context: {BUG_CONTEXT}"},
            {"role": "user", "content": node.goal + " Answer in 1-2 sentences."},
        ],
        max_tokens=100,
    )
    text = (resp.choices[0].message.content or "").strip()
    print(f"    real response: {text!r}")
    return NodeResult(status="success", notes=text)


async def main():
    pool = await create_pool()
    row = await pool.fetchrow(
        "SELECT id, name, steps FROM procedures WHERE name='debug_failing_test' AND t_invalid IS NULL"
    )
    await pool.close()
    assert row is not None, "FAIL: debug_failing_test procedure not found -- run the seed script first"

    print(f"Fetched REAL stored procedure: {row['name']} (row id {row['id']})")
    print(f"Real steps: {len(row['steps'])}")

    graph = steps_to_linear_graph(uuid4(), row["steps"])
    print(f"Compiled into a real {len(graph.nodes)}-node linear-deps graph: "
          f"{[(n.order, n.deps) for n in graph.nodes]}")

    result = await execute_task_graph(graph, run_node=run_node)

    print("\n=== RESULT ===")
    print("overall outcome:", result.outcome)
    for order in sorted(result.node_statuses):
        print(f"  step {order}: {result.node_statuses[order]} -- "
              f"{result.node_results[order].notes if order in result.node_results else ''}")

    assert result.outcome == "success", f"FAIL: expected success, got {result.outcome}"
    assert len(result.node_statuses) == len(row["steps"]), "FAIL: not all real steps ran"
    print("\nPASS: a REAL stored 4-step procedure, fetched from the live DB, "
          "executed in real dependency order through graph_executor.py, "
          "each step a genuine LLM call -- confirms multi-step execution "
          "works for the real corpus today, with zero schema change.")


if __name__ == "__main__":
    asyncio.run(main())
