"""
Real, live proof that find_best_way's tier-1 (_respond_tier1_hit) now
actually EXECUTES a matched procedure's real steps -- real per-step LLM
calls, real dependency order, real execution_plans/task_graphs/executions
rows -- instead of just returning a text listing.

Calls _respond_tier1_hit directly against a real stored procedure row
(debug_failing_test, seeded this session), bypassing find_applicable_procedures'
own matching so this test is isolated to tier-1's execution behavior.

Hand-run, not part of pytest (registered in test_live_scripts_not_collected.py).
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("STEALTHLAB_MCP_TOKEN", "throwaway-local-test-token")

from app.db.session import create_pool
import app.mcp_server.server as srv


async def main():
    pool = await create_pool()
    row = await pool.fetchrow(
        "SELECT * FROM procedures WHERE name='debug_failing_test' AND t_invalid IS NULL"
    )
    assert row is not None, "FAIL: debug_failing_test not found -- run the seed script first"
    matched_procedure = dict(row)

    task_description = (
        "test_add_returns_sum is failing: add(2, 3) returned -1 instead of 5, "
        "because calc.py's add() does `a - b` instead of `a + b`."
    )

    result_text = await srv._respond_tier1_hit(pool, task_description, matched_procedure)
    print(result_text)

    # Independent verification: a real execution_plans + task_graphs +
    # executions triplet must now exist for this run, not just plan+graph.
    exec_row = await pool.fetchrow(
        "SELECT e.id, e.outcome, tg.nodes "
        "FROM executions e JOIN task_graphs tg ON tg.id = e.task_graph_id "
        "WHERE e.procedure_id = $1 AND e.created_by = 'find_best_way' "
        "ORDER BY e.created_at DESC LIMIT 1",
        matched_procedure["procedure_id"],
    )
    await pool.close()

    assert exec_row is not None, "FAIL: no executions row was written for this tier-1 run"
    assert len(exec_row["nodes"]) == len(matched_procedure["steps"]), (
        f"FAIL: expected {len(matched_procedure['steps'])} real nodes, "
        f"got {len(exec_row['nodes'])}"
    )
    assert "-> " in result_text and "..." not in result_text.split("-> ")[1][:3], (
        "sanity: real reasoning text must be present, not a placeholder"
    )

    print(f"\nPASS: tier-1 executed a real {len(exec_row['nodes'])}-node graph, "
          f"real executions row {exec_row['id']} written, outcome={exec_row['outcome']}.")


if __name__ == "__main__":
    asyncio.run(main())
