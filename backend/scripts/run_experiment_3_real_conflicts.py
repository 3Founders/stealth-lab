"""
Runs the real debate pipeline against the two GENUINE conflicts found by
directly searching the real tau2-bench banking_knowledge corpus (not
synthetic, not incidental embedding-similarity false positives):

  1. Business Checking Account Promotion: October 2025 vs November 2025
  2. Business Savings Account Promotion: October 2025 vs November 2025

Both are real internal promotion documents with OVERLAPPING validity
windows (Oct: 10/12-11/12, Nov: 11/01-11/30) that assert different
"#1 recommended account" during the overlap -- a genuine, unambiguous
conflict, not a template-similarity artifact. First real-corpus test of
whether the debate mechanism can actually RESOLVE a real conflict, not
just correctly decline a false one (which is all 48+ pairs from the
random batch run demonstrated so far).

Looks up the real node ids by their known document ids (from the
tau2-bench source files) rather than by name match, since dedup/hierarchy
may have changed exact row ids since ingestion.

Prints full detail for both -- only 2 pairs, worth reading in full
rather than a summary line.

Run from backend/:
    python scripts/run_experiment_3_real_conflicts.py
    python scripts/run_experiment_3_real_conflicts.py --apply
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
from app.debate.panel import default_judge, default_layer2_agent, default_panel
from app.models.change import ChangeSet
from app.services.knowledge_conflict import create_conflict_trigger_for_pair
from app.services.knowledge_update import KnowledgeUpdater
from app.services.loop import LoopOrchestrator

# doc_id -> (title, category) from direct inspection of the real source files
REAL_PAIRS = [
    ("Internal: Business Checking Account Promotion - October 2025",
     "Internal: Business Checking Account Promotion - November 2025",
     "Business Checking Account Promotion: October vs November 2025"),
    ("Internal: Business Savings Account Promotion - October 2025",
     "Internal: Business Savings Account Promotion - November 2025",
     "Business Savings Account Promotion: October vs November 2025"),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )
    results = []

    # Match by known titles directly -- more reliable than doc_id, since
    # doc_id was never persisted as a separate column.
    for title_a, title_b, label in REAL_PAIRS:
        print(f"\n{'='*70}\n{label}\n{'='*70}")
        row_a = await pool.fetchrow(
            "SELECT id, name, properties->>'content' AS content FROM knowledge_nodes "
            "WHERE t_invalid IS NULL AND name = $1", title_a,
        )
        row_b = await pool.fetchrow(
            "SELECT id, name, properties->>'content' AS content FROM knowledge_nodes "
            "WHERE t_invalid IS NULL AND name = $1", title_b,
        )
        if not row_a or not row_b:
            print(f"  COULD NOT FIND one or both nodes (a={bool(row_a)}, b={bool(row_b)}) "
                  f"-- is banking_knowledge still ingested in this database?")
            continue

        print(f"  A ({row_a['id']}): {row_a['content'][:200]}...")
        print(f"  B ({row_b['id']}): {row_b['content'][:200]}...")

        # These are a REAL, known conflict -- create the trigger directly
        # rather than relying on embedding-band auto-detection, which is
        # not guaranteed to have flagged this exact pair.
        trigger_id = await create_conflict_trigger_for_pair(
            pool, str(row_a["id"]), str(row_b["id"]), similarity=1.0,  # similarity irrelevant here, real known conflict
        )
        print(f"\n  trigger: {trigger_id}")

        scorecards = await orchestrator.run(trigger_id)
        if not scorecards:
            print("  -> no scoreable candidates")
            results.append({
                "pair": {"id_a": str(row_a["id"]), "id_b": str(row_b["id"]),
                          "name_a": title_a, "name_b": title_b,
                          "category_a": None, "category_b": None, "similarity": 1.0},
                "outcome": "no_candidates", "trigger_id": str(trigger_id),
            })
            continue

        best = None
        for sc in scorecards:
            if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
                best = sc

        if best is None:
            print(f"  -> {len(scorecards)} candidate(s), none passed Layer 1")
            results.append({
                "pair": {"id_a": str(row_a["id"]), "id_b": str(row_b["id"]),
                          "name_a": title_a, "name_b": title_b,
                          "category_a": None, "category_b": None, "similarity": 1.0},
                "outcome": "no_pass", "n_candidates": len(scorecards), "trigger_id": str(trigger_id),
            })
            continue

        candidate_row = await pool.fetchrow("SELECT rationale, change_set FROM candidates WHERE id = $1", best.candidate_id)
        print(f"\n  PASSED: groundedness={best.layer1.groundedness_score:.2f}")
        print(f"  rationale: {candidate_row['rationale'][:600]}")
        print(f"\n  change_set:\n{json.dumps(candidate_row['change_set'], indent=2)}")

        if args.apply:
            change_set = ChangeSet.model_validate(candidate_row["change_set"])
            updater = KnowledgeUpdater(pool)
            applied = await updater.apply(change_set, approver_id="experiment_3_real_conflicts")
            print(f"\n  APPLIED: {applied}")
        else:
            print("\n  (dry run -- pass --apply to actually apply)")

        results.append({
            "pair": {
                "id_a": str(row_a["id"]), "id_b": str(row_b["id"]),
                "name_a": title_a, "name_b": title_b,
                "category_a": None, "category_b": None, "similarity": 1.0,
            },
            "outcome": "passed",
            "groundedness": best.layer1.groundedness_score,
            "candidate_id": str(best.candidate_id),
            "trigger_id": str(trigger_id),
        })

    await pool.close()

    with open("experiment_3_real_conflicts_results.json", "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote experiment_3_real_conflicts_results.json ({len(results)} result(s)) -- "
          f"point the checkers at this file, not the default batch results")


if __name__ == "__main__":
    asyncio.run(main())
