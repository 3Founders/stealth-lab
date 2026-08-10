"""
Independent, mechanical check for temporal-overlap conflicts: parses
"ACTIVE FROM X TO Y" (and similar) date ranges directly from both source
documents, checks whether they actually overlap using real date math --
not LLM judgment -- and flags any 'passed' candidate that had a real,
detectable overlap but whose resolution shows no evidence of addressing
it (no precedence/supersession language).

Exists because of a real, confirmed failure: two promotion documents
with genuinely overlapping active windows and different top-recommended
products got diagnosed as an unrelated "false positive" by the panel,
and groundedness scoring (which only checks citation accuracy, not
diagnostic correctness) did not catch it. The prompt now explicitly
requires cross-referencing date ranges before concluding false-positive
-- this script is the independent check that doesn't just trust that
instruction was followed, the same "verify, don't just ask nicely"
principle as verify_change_set_grounding.py.

Narrower than a general conflict-diagnosis checker, deliberately: this
only catches the SPECIFIC failure mode already confirmed real (date-
range overlap). It says nothing about conflicts with no stated dates.

Run from backend/, after a run has written a results json with trigger_id
included:
    python scripts/verify_temporal_conflict_handling.py --results experiment_3_real_conflicts_results.json
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.temporal_conflict import parse_date_range

PRECEDENCE_KEYWORDS = (
    "supersede", "supersedes", "precedence", "takes priority", "overlap",
    "during the overlap", "prioritize", "takes effect over",
)


def ranges_overlap(a, b) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def resolution_addresses_overlap(rationale: str, change_set: dict) -> bool:
    text = (rationale or "").lower()
    for op in change_set.get("ops", []):
        text += " " + json.dumps(op.get("changes", {})).lower()
        text += " " + (op.get("reason") or "").lower()
    return any(kw in text for kw in PRECEDENCE_KEYWORDS)


async def check_pair(pool, id_a: str, id_b: str, rationale: str, change_set: dict, label: str):
    rows = await pool.fetch(
        "SELECT id, properties->>'content' AS content FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
        [id_a, id_b],
    )
    contents = {str(r["id"]): r["content"] for r in rows}
    range_a = parse_date_range(contents.get(id_a, ""))
    range_b = parse_date_range(contents.get(id_b, ""))

    if range_a is None or range_b is None:
        print(f"  [{label}] no parseable date range on one or both sides -- this checker doesn't apply here")
        return None

    overlap = ranges_overlap(range_a, range_b)
    print(f"  [{label}] source range A: {range_a[0].date()}-{range_a[1].date()}, "
          f"B: {range_b[0].date()}-{range_b[1].date()} -- overlap: {overlap}")

    if not overlap:
        print(f"    no real overlap -- 'false positive' or independent scope IS the correct diagnosis here")
        return "no_overlap_correctly_no_action_needed"

    addressed = resolution_addresses_overlap(rationale, change_set)
    if addressed:
        print(f"    REAL OVERLAP, correctly addressed in the resolution (precedence language found)")
        return "overlap_correctly_addressed"
    else:
        print(f"    *** REAL OVERLAP DETECTED, but resolution shows NO precedence/supersession language ***")
        print(f"    *** This is very likely the same misdiagnosis failure mode as the confirmed real case ***")
        return "overlap_NOT_addressed_FLAG_FOR_REVIEW"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="experiment_3_batch_results.json")
    args = parser.parse_args()

    pool = await create_pool(os.environ["DATABASE_URL"])
    results = json.load(open(args.results))

    flagged = []
    for r in results:
        if r.get("outcome") != "passed":
            continue
        pair = r["pair"]
        candidate_row = await pool.fetchrow(
            "SELECT rationale, change_set FROM candidates WHERE id = $1", r["candidate_id"]
        )
        if not candidate_row:
            continue
        label = f"{pair['name_a']} vs {pair['name_b']}"
        verdict = await check_pair(
            pool, pair["id_a"], pair["id_b"],
            candidate_row["rationale"], candidate_row["change_set"], label,
        )
        if verdict == "overlap_NOT_addressed_FLAG_FOR_REVIEW":
            flagged.append({"pair": label, "candidate_id": r["candidate_id"]})

    await pool.close()
    print(f"\n=== {len(flagged)} candidate(s) flagged: real date overlap detected, not addressed ===")
    for f in flagged:
        print(f"  {f['pair']}  (candidate {f['candidate_id']})")


if __name__ == "__main__":
    asyncio.run(main())
