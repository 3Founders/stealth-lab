"""
Inspects the most recent debate's candidates and scorecards directly
from the database -- no new LLM calls, just reads what the last run
already persisted. Use this to see exactly WHAT a candidate proposed
and WHY it failed Layer 1, instead of re-running (and re-paying for)
the debate.

Run from backend/:
    python scripts/inspect_experiment_3_debate.py
    python scripts/inspect_experiment_3_debate.py --trigger-id <uuid>   # a specific run
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger-id", default=None)
    args = parser.parse_args()

    pool = await create_pool(os.environ["DATABASE_URL"])

    if args.trigger_id:
        trigger = await pool.fetchrow("SELECT * FROM triggers WHERE id = $1", args.trigger_id)
    else:
        trigger = await pool.fetchrow(
            "SELECT * FROM triggers WHERE rule_name = 'knowledge_conflict' "
            "ORDER BY detected_at DESC LIMIT 1"
        )
    if trigger is None:
        print("no knowledge_conflict trigger found")
        return

    print(f"trigger: {trigger['id']}  detail: {trigger['detail']}")
    debate_id = trigger["debate_id"]
    if debate_id is None:
        print("no debate was opened for this trigger")
        return

    debate = await pool.fetchrow("SELECT * FROM debates WHERE id = $1", debate_id)
    print(f"debate state: {debate['state']}  termination: {debate['termination_reason']}\n")

    candidates = await pool.fetch("SELECT * FROM candidates WHERE debate_id = $1", debate_id)
    scorecards = await pool.fetch("SELECT * FROM scorecards WHERE debate_id = $1", debate_id)
    scorecards_by_candidate = {sc["candidate_id"]: sc for sc in scorecards}

    for c in candidates:
        print(f"=== candidate {c['id']} ===")
        print(f"summary: {c['summary']}")
        print(f"rationale: {c['rationale'][:500]}")
        print(f"change_set:\n{json.dumps(c['change_set'], indent=2)}")

        sc = scorecards_by_candidate.get(c["id"])
        if sc:
            print(f"\nlayer1_passed: {sc['layer1_passed']}")
            print(f"groundedness_score: {sc['groundedness_score']}")
            print(f"fallacy_flags: {sc['fallacy_flags']}")
            print(f"constructive: {sc['constructive']}")
            print(f"unresolved_cites: {sc['unresolved_cites']}")
        print()

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())