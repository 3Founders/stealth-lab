"""
Decomposition promotion and human decision, verified against real
Postgres.

The case that matters most here: an agent can be *approved* (the graph
structure passed review) while remaining *not runnable* (a constituent
step's skill doesn't actually resolve). Those are deliberately separate
facts -- this check exists to prove the distinction is real, not just
asserted in a docstring.

Run:
    DATABASE_URL=postgresql://... python integration_check_v2_agent_promotion.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.services.agent_decision import AgentNotPendingApproval, decide_agent
from app.services.agent_promotion import DecompositionNotApproved, promote_decomposition
from app.services.agent_review_state_machine import AgentReviewStateMachine
from app.services.execution import default_registry


class GoodJudge:
    agent_id, model_id, family = "judge", "mock", "mockfam"

    async def respond(self, system, user):
        return json.dumps({"fallacy_flags": [], "constructive": True, "notes": "fine"})


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])
    machine = AgentReviewStateMachine(pool)
    registry = default_registry()
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    print("-- promoting a real, approved, single-step decomposition --")
    real_task = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ('Step one', 'echo') RETURNING id"
    )
    deco = await pool.fetchrow(
        "INSERT INTO decompositions (problem, feasible, reasoning, change_set, "
        "status, applied_refs) VALUES "
        "('A real problem needing one step', true, 'A clean, single-step plan', "
        "$1, 'approved', $2) RETURNING id",
        {"ops": [{"op_type": "create_task_node", "ref": "t1", "name": "Step one"}]},
        {"t1": str(real_task["id"])},
    )
    outcome = await promote_decomposition(pool, deco["id"], GoodJudge(), actor="tester")
    check("passed review despite zero citations -- the correct outcome for "
          "created content, not a citation-based claim",
          outcome["review"].passed is True)
    state = await machine.current_state(outcome["agent_id"])
    check("reached pending_human_approval", state == "pending_human_approval", state)

    print()
    print("-- refuses to promote a decomposition that was never approved --")
    unapproved = await pool.fetchrow(
        "INSERT INTO decompositions (problem, feasible, change_set, status) "
        "VALUES ('unapproved', true, '{\"ops\":[]}', 'proposed') RETURNING id"
    )
    blocked = False
    try:
        await promote_decomposition(pool, unapproved["id"], GoodJudge())
    except DecompositionNotApproved:
        blocked = True
    check("refused", blocked)

    print()
    print("-- human approval where the constituent skill genuinely resolves --")
    decision = await decide_agent(pool, outcome["agent_id"], "approved", registry, actor="approver")
    check("runnable=True", decision["runnable"] is True)

    print()
    print("-- the case that matters most: approved, but NOT runnable --")
    unresolvable_task = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ('Step two', 'nonexistent_skill') "
        "RETURNING id"
    )
    deco2 = await pool.fetchrow(
        "INSERT INTO decompositions (problem, feasible, change_set, status, applied_refs) "
        "VALUES ('another problem', true, $1, 'approved', $2) RETURNING id",
        {"ops": [{"op_type": "create_task_node", "ref": "t2", "name": "Step two"}]},
        {"t2": str(unresolvable_task["id"])},
    )
    outcome2 = await promote_decomposition(pool, deco2["id"], GoodJudge())
    check("passed review -- the graph structure itself is fine",
          outcome2["review"].passed is True)
    decision2 = await decide_agent(pool, outcome2["agent_id"], "approved", registry, actor="approver")
    check("review_state is approved", decision2["review_state"] == "approved")
    check("runnable=False -- the skill it depends on doesn't exist",
          decision2["runnable"] is False)

    print()
    print("-- cannot re-decide an already-decided agent --")
    blocked2 = False
    try:
        await decide_agent(pool, outcome2["agent_id"], "approved", registry)
    except AgentNotPendingApproval:
        blocked2 = True
    check("refused", blocked2)

    await pool.close()
    print(f"\n{'=' * 55}")
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("AGENT PROMOTION AND DECISION VERIFIED against real Postgres.")


if __name__ == "__main__":
    asyncio.run(main())
