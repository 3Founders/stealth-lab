"""
Agent review orchestrator, verified against real Postgres.

Covers the four cases that matter: a genuinely well-grounded proposal
passing and reaching human review, a genuinely fallacious one being
rejected outright with the reason logged (not left pending), a
wrong-source call being refused as a programming error rather than
silently reviewed, and that review results actually persist.

Run:
    DATABASE_URL=postgresql://... python integration_check_v2_agent_review.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.models.change import ChangeSet
from app.models.debate import Citation
from app.services.agent_review_orchestrator import AgentReviewOrchestrator, WrongReviewPath
from app.services.agent_review_state_machine import AgentReviewStateMachine


class GoodJudge:
    agent_id, model_id, family = "judge", "mock", "mockfam"

    async def respond(self, system, user):
        return json.dumps({"fallacy_flags": [], "constructive": True, "notes": "looks fine"})


class BadJudge:
    agent_id, model_id, family = "judge", "mock", "mockfam"

    async def respond(self, system, user):
        return json.dumps({
            "fallacy_flags": [{
                "fallacy": "asiddha", "quote": "the unproven premise",
                "explanation": "no support for this",
            }],
            "constructive": True, "notes": "found a real problem",
        })


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])
    machine = AgentReviewStateMachine(pool)
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    real_task = await pool.fetchrow(
        "INSERT INTO task_nodes (name) VALUES ('Some real task') RETURNING id"
    )
    change_set = ChangeSet(ops=[{"op_type": "create_task_node", "ref": "t1", "name": "New step"}])

    print("-- a genuinely well-grounded proposal --")
    agent_good = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, skill_ref) "
        "VALUES ('Good agent', 'desc', 'graph_derived', 'local_skill', 'x') RETURNING id"
    )
    cite = Citation(node_id=real_task["id"], node_table="task_nodes")
    orch_good = AgentReviewOrchestrator(pool, GoodJudge())
    review_good = await orch_good.review_graph_derived(
        agent_good["id"], "summary grounded in a real task", "rationale",
        change_set, cited=[cite],
    )
    check("passed", review_good.passed is True, f"groundedness={review_good.groundedness_score}")
    state_good = await machine.current_state(agent_good["id"])
    check("reached pending_human_approval", state_good == "pending_human_approval", state_good)

    print()
    print("-- a genuinely fallacious proposal --")
    agent_bad = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, skill_ref) "
        "VALUES ('Bad agent', 'desc', 'graph_derived', 'local_skill', 'y') RETURNING id"
    )
    orch_bad = AgentReviewOrchestrator(pool, BadJudge())
    review_bad = await orch_bad.review_graph_derived(
        agent_bad["id"], "summary", "rationale", change_set, cited=[cite],
    )
    check("did not pass", review_bad.passed is False)
    check("fallacy captured", len(review_bad.fallacy_flags) == 1)
    state_bad = await machine.current_state(agent_bad["id"])
    check("rejected outright, not left pending", state_bad == "rejected", state_bad)

    history = await machine.history(agent_bad["id"])
    reasons = [h["reason"] for h in history]
    check("rejection reason names the fallacy",
          any("fallacy" in (r or "") for r in reasons), reasons)

    print()
    print("-- calling the graph-derived review path on a code-sourced agent --")
    agent_external = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, skill_ref) "
        "VALUES ('External agent', 'desc', 'external_marketplace', 'local_skill', 'z') "
        "RETURNING id"
    )
    orch_external = AgentReviewOrchestrator(pool, GoodJudge())
    blocked = False
    try:
        await orch_external.review_graph_derived(agent_external["id"], "s", "r", change_set)
    except WrongReviewPath:
        blocked = True
    check("refused as a programming error, not silently reviewed", blocked)

    print()
    print("-- review results actually persisted --")
    count = await pool.fetchval("SELECT COUNT(*) FROM agent_reviews")
    check("two review rows exist", count == 2, f"got {count}")

    await pool.close()
    print(f"\n{'=' * 55}")
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("AGENT REVIEW ORCHESTRATOR VERIFIED against real Postgres.")


if __name__ == "__main__":
    asyncio.run(main())
