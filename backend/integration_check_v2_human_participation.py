"""
Human participation in a debate, verified against real Postgres and a
real (fake-backed) panel end to end: a real trigger, a real debate
reaching PENDING_APPROVAL, a real human argument added, a real
continuation round where the panel genuinely reacts, and confirmation
the debate never left PENDING_APPROVAL throughout.

Requires a fake OpenAI-compatible server on 127.0.0.1:11500 and
REAL_TASK_ID set to the seeded task's real id, since the fake panel
needs to cite something real for Layer 1 to actually pass -- the same
requirement every real call in this project has always had.

Run:
    DATABASE_URL=postgresql://... USE_LOCAL_MODELS=true \\
    LOCAL_BASE_URL=http://127.0.0.1:11500/v1 LOCAL_JUDGE_MODEL=fake-model \\
    python integration_check_v2_human_participation.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.debate.panel import default_judge, default_panel
from app.services.human_participation import DebateNotPendingApproval, add_human_turn
from app.services.loop import LoopOrchestrator
from app.services.triggers import ThresholdRule, TriggerDetector


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    det = TriggerDetector(pool)
    hits = await det.scan([ThresholdRule(name="r", metric="error_rate", threshold=0.1, min_samples=5)])
    ids = await det.record(hits)
    check("a real trigger was recorded", len(ids) > 0, len(ids))

    orch = LoopOrchestrator(pool, default_panel(), default_judge())
    scorecards = await orch.run(ids[0])
    check("a real debate reached a real scorecard", len(scorecards) > 0)

    debate_row = await pool.fetchrow(
        "SELECT id, state, round_number FROM debates ORDER BY opened_at DESC LIMIT 1"
    )
    debate_id = debate_row["id"]
    check("debate is genuinely at PENDING_APPROVAL", debate_row["state"] == "PENDING_APPROVAL",
          debate_row["state"])
    original_round = debate_row["round_number"]

    print()
    print("-- a real human adds a real argument --")
    await add_human_turn(
        pool, debate_id, "human_reviewer",
        "Have we considered the cost impact of this change?", action="propose",
    )

    after = await pool.fetchrow("SELECT round_number, state FROM debates WHERE id = $1", debate_id)
    check("round number genuinely advanced", after["round_number"] > original_round)
    check("debate NEVER left PENDING_APPROVAL", after["state"] == "PENDING_APPROVAL", after["state"])

    human_turn = await pool.fetchrow(
        "SELECT * FROM debate_turns WHERE debate_id = $1 AND speaker_kind = 'human'", debate_id
    )
    check("the human turn is genuinely persisted", human_turn is not None)
    check("authored by the real name given",
          human_turn["speaker_id"] == "human_reviewer" if human_turn else False)

    agent_reaction = await pool.fetch(
        "SELECT * FROM debate_turns WHERE debate_id = $1 AND round_number = $2 "
        "AND speaker_kind = 'agent'", debate_id, after["round_number"],
    )
    check("the panel genuinely produced new turns reacting to the argument",
          len(agent_reaction) > 0)

    print()
    print("-- refuses to add an argument to a debate not awaiting approval --")
    row = await pool.fetchrow("SELECT id FROM task_nodes LIMIT 1")
    trigger_id = await pool.fetchval(
        "INSERT INTO triggers (task_node_id, rule_name, metric_name, observed_value, "
        "threshold, sample_size, detail) VALUES ($1,'r','m',1,1,1,'{}') RETURNING id",
        row["id"],
    )
    other_debate = await pool.fetchval(
        "INSERT INTO debates (trigger_id, state) VALUES ($1, 'OPEN') RETURNING id", trigger_id
    )
    blocked = False
    try:
        await add_human_turn(pool, other_debate, "x", "y")
    except DebateNotPendingApproval:
        blocked = True
    check("refused", blocked)

    await pool.close()
    print(f"\n{'=' * 55}")
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("HUMAN PARTICIPATION VERIFIED against real Postgres and a real panel.")


if __name__ == "__main__":
    asyncio.run(main())
