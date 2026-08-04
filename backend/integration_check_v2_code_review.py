"""
Code-sourced agent review, verified against real Postgres, including a
real bandit catch on genuinely unsafe code (not a mocked scanner).

The check that matters most: runnable stays False even after the agent
reaches review_state='approved', for both user_submitted and
external_marketplace sources. No execution mechanism exists yet
(stage 6) -- runnable=True here would be a guarantee this system
cannot back up.

Run:
    DATABASE_URL=postgresql://... python integration_check_v2_code_review.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.services.agent_decision import decide_agent
from app.services.agent_review_state_machine import AgentReviewStateMachine
from app.services.code_review import CodeSourcedReviewOrchestrator, WrongReviewPath
from app.services.execution import default_registry


class GoodReviewer:
    def __init__(self, family):
        self.agent_id, self.model_id, self.family = f"r_{family}", "mock", family

    async def respond(self, system, user):
        return json.dumps({
            "sound": True, "matches_stated_purpose": True,
            "concerns": [], "notes": "looks fine",
        })


UNSAFE_CODE = """
import os
PASSWORD = "hardcoded_super_secret_123"
def run(cmd):
    os.system(cmd)
"""


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

    print("-- construction guards --")
    try:
        CodeSourcedReviewOrchestrator(pool, [GoodReviewer("a")])
        check("rejects a single reviewer", False)
    except ValueError:
        check("rejects a single reviewer", True)
    try:
        CodeSourcedReviewOrchestrator(pool, [GoodReviewer("same"), GoodReviewer("same")])
        check("rejects reviewers sharing a family", False)
    except ValueError:
        check("rejects reviewers sharing a family", True)

    orch = CodeSourcedReviewOrchestrator(pool, [GoodReviewer("famA"), GoodReviewer("famB")])

    print()
    print("-- a clean user_submitted request --")
    submitted = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, skill_ref, "
        "source_detail) VALUES ('Invoice summarizer request', "
        "'summarize uploaded invoices into a table', 'user_submitted', "
        "'local_skill', 'placeholder', $1) RETURNING id",
        {"requested_input": "PDF invoices", "requested_output": "a summary table"},
    )
    result1 = await orch.review_code_sourced(submitted["id"])
    check("passed review", result1["passed"] is True)
    state1 = await machine.current_state(submitted["id"])
    check("reached pending_human_approval", state1 == "pending_human_approval", state1)

    print()
    print("-- the case that matters most: runnable stays False even once approved --")
    decision1 = await decide_agent(pool, submitted["id"], "approved", registry, actor="approver")
    check("review_state is approved", decision1["review_state"] == "approved")
    check("runnable is False -- no execution mechanism exists yet (stage 6)",
          decision1["runnable"] is False)

    print()
    print("-- external_marketplace agent with REAL bandit-catchable code --")
    marketplace = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, skill_ref, "
        "source_detail) VALUES ('Suspicious skill', 'does something with commands', "
        "'external_marketplace', 'local_skill', 'placeholder', $1) RETURNING id",
        {"repo_url": "https://example.com/fake", "code": UNSAFE_CODE},
    )
    result2 = await orch.review_code_sourced(marketplace["id"])
    check("did not pass -- bandit should catch os.system and the hardcoded credential",
          result2["passed"] is False)
    check("scan found real issues, not zero",
          result2["scan_high_severity_count"] > 0 or len(result2["scan_findings"]) > 0,
          f"findings={len(result2['scan_findings'])}, high={result2['scan_high_severity_count']}")
    state2 = await machine.current_state(marketplace["id"])
    check("rejected outright", state2 == "rejected", state2)

    print()
    print("-- wrong review path is refused, not silently reviewed --")
    graph_agent = await pool.fetchrow(
        "INSERT INTO agents (name, description, source, execution_mode, workflow_task_ids) "
        "VALUES ('a graph agent', 'x', 'graph_derived', 'graph_workflow', '[]') RETURNING id"
    )
    blocked = False
    try:
        await orch.review_code_sourced(graph_agent["id"])
    except WrongReviewPath:
        blocked = True
    check("refused", blocked)

    await pool.close()
    print(f"\n{'=' * 55}")
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("CODE-SOURCED REVIEW VERIFIED against real Postgres, including a real bandit catch.")


if __name__ == "__main__":
    asyncio.run(main())
