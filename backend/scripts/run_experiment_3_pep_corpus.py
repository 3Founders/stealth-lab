"""
Runs the real debate pipeline against ALL real PEP Superseded-By pairs
(scripts/pep_supersession_ground_truth.json, written by ingest_peps.py) --
n=32 real, machine-labeled conflicts, vs. the n=2 incidentally-discovered
pairs banking_knowledge could offer. This is the actual density Experiment
3 needed and never had.

WHY THIS TEST IS STRONGER THAN THE BANKING ONE, MECHANICALLY:
banking_knowledge's ground truth required a human (us) to read prose and
judge whether the resolution "addressed" a date overlap -- inherently
fuzzy. Here, ground truth is binary and objective: PEP X's own
Superseded-By header says X is superseded by Y. So this script can
AUTOMATICALLY classify every resolution against that ground truth, not
just check groundedness:

  CORRECT       - an update_knowledge_node op targets the ACTUALLY-
                  superseded PEP (knowledge_node_id == superseded_id),
                  i.e. the panel got the direction right.
  WRONG_DIRECTION - an update_knowledge_node op targets the superseding
                  (Active/successor) PEP instead -- the panel superseded
                  the wrong side. A real, structurally serious failure
                  mode this can now actually detect, that the n=2 banking
                  sample was too small to ever surface.
  FALSE_POSITIVE_MISDIAGNOSIS - no update_knowledge_node op touches
                  either node at all (the exact failure mode confirmed
                  real in the banking savings-pair case) -- objectively
                  wrong here, since every pair in this file is a REAL,
                  machine-confirmed supersession, not a maybe.
  NO_PASS / NO_CANDIDATES - Layer 1 rejected everything, or nothing
                  scoreable came back.

This classification is a NECESSARY check, not a sufficient one -- it only
confirms the panel picked the right node and the right direction. It says
nothing about whether the actual change_set content is well-written.
Still run verify_change_set_grounding.py against this file's output
afterward for that -- same "verify, don't just ask nicely" layering as
before, not a replacement for it.

Run from backend/:
    python scripts/run_experiment_3_pep_corpus.py
    python scripts/run_experiment_3_pep_corpus.py --limit 5      # smoke test first
    python scripts/run_experiment_3_pep_corpus.py --apply        # actually apply passing resolutions
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
from app.models.change import ChangeSet, UpdateKnowledgeNodeOp
from app.services.knowledge_conflict import create_conflict_trigger_for_pair
from app.services.knowledge_update import KnowledgeUpdater
from app.services.loop import LoopOrchestrator

GROUND_TRUTH_FILE = os.path.join(os.path.dirname(__file__), "pep_supersession_ground_truth.json")


def classify_resolution(
    change_set_dict: dict, superseded_id: str, superseding_id: str,
    no_action_justified: bool = False,
) -> str:
    """
    Pure classification against known ground truth -- no LLM judgment
    involved here, deliberately, same principle as
    verify_temporal_conflict_handling.py: don't trust the panel's own
    stated diagnosis, check the actual structural fact (which node id
    the update op targets) against what we independently know is true.
    """
    try:
        cs = ChangeSet.model_validate(change_set_dict)
    except Exception:
        return "UNPARSEABLE_CHANGE_SET"

    update_targets = {
        str(op.knowledge_node_id) for op in cs.ops if isinstance(op, UpdateKnowledgeNodeOp)
    }
    if not update_targets:
        if no_action_justified:
            # Distinct from FALSE_POSITIVE_MISDIAGNOSIS on purpose: every
            # pair in this corpus is a REAL, confirmed supersession, so a
            # judge-approved "no action needed" is a legitimate outcome
            # this mechanical check simply cannot verify the direction
            # of (there is no update op to check a target id against).
            # Folding it into either CORRECT or FALSE_POSITIVE_
            # MISDIAGNOSIS would overstate what was actually confirmed.
            return "NO_ACTION_JUSTIFIED_UNVERIFIED"
        return "FALSE_POSITIVE_MISDIAGNOSIS"
    if superseded_id in update_targets:
        return "CORRECT"
    if superseding_id in update_targets:
        return "WRONG_DIRECTION"
    return "OTHER_TARGET"  # touched neither known id -- shouldn't happen, worth seeing if it does


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                         help="only run the first N pairs -- use for a cheap smoke test "
                              "before committing to all 32 real API-call rounds")
    parser.add_argument("--ground-truth", default=GROUND_TRUTH_FILE)
    parser.add_argument("--out", default=None,
                         help="defaults to experiment_3_pep_corpus_results.json for a full run, "
                              "or experiment_3_pep_corpus_results_limitN.json when --limit is set "
                              "-- a --limit run must NOT silently overwrite a full run's output "
                              "under the same filename (this happened for real: a --limit 5 smoke "
                              "test after a prompt fix clobbered the original 32-pair run's file, "
                              "destroying the only record of which 5 pairs had failed and why)")
    parser.add_argument("--pep-numbers", default=None,
                         help="comma-separated superseded PEP numbers to run ONLY those pairs, "
                              "e.g. --pep-numbers 248,345,386,438,563 -- for re-testing specific "
                              "known pairs (from a prior run's NO_PASS/NO_CANDIDATES) without "
                              "re-running the full 32 and burning API calls on pairs already known good")
    parser.add_argument("--pair-delay", type=float, default=3.0,
                         help="seconds to wait between pairs -- gather_responses now retries "
                              "429s within a round with backoff, but back-to-back pairs with "
                              "zero gap still stack pressure on the same rate limit; a real "
                              "5-pair run hit 429s repeatedly with no pacing at all before this")
    args = parser.parse_args()
    if args.out is None:
        args.out = (f"experiment_3_pep_corpus_results_limit{args.limit}.json" if args.limit
                    else "experiment_3_pep_corpus_results.json")

    if os.path.exists(args.out):
        print(f"WARNING: {args.out} already exists and will be OVERWRITTEN by this run. "
              f"This has actually destroyed real data before (a --limit smoke test clobbered "
              f"a full 32-pair run's only record of which pairs failed). Ctrl+C now within 5s "
              f"to abort, or pass --out to write somewhere else.")
        import time
        time.sleep(5)

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pairs = json.load(open(args.ground_truth))
    # Guard against a pair whose id didn't resolve at ingestion time (the
    # ingestion script writes literal "None" via str(None) if a key was
    # missing from result.knowledge_ids -- confirmed real possibility,
    # not assumed, since Onboarder.seed only populates ids it actually
    # inserted).
    usable = [p for p in pairs if p["superseded_id"] != "None" and p["superseding_id"] != "None"]
    if len(usable) < len(pairs):
        print(f"WARNING: {len(pairs) - len(usable)} pair(s) had an unresolved id, skipping those")
    if args.pep_numbers:
        wanted = {int(n.strip()) for n in args.pep_numbers.split(",")}
        usable = [p for p in usable if p["superseded_number"] in wanted]
        found = {p["superseded_number"] for p in usable}
        missing = wanted - found
        if missing:
            print(f"WARNING: requested PEP number(s) not found in ground truth: {sorted(missing)}")
    if args.limit:
        usable = usable[: args.limit]
    print(f"running {len(usable)} of {len(pairs)} real PEP supersession pairs -> writing to {args.out}")

    pool = await create_pool(os.environ["DATABASE_URL"])
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )
    results = []
    tally: dict[str, int] = {}

    for i, pair in enumerate(usable, 1):
        if i > 1 and args.pair_delay > 0:
            await asyncio.sleep(args.pair_delay)

        label = f"PEP {pair['superseded_number']} -> superseded by PEP {pair['superseding_number']}"
        print(f"\n{'='*70}\n[{i}/{len(usable)}] {label}\n{'='*70}")

        trigger_id = await create_conflict_trigger_for_pair(
            pool, pair["superseded_id"], pair["superseding_id"], similarity=1.0,
            approver_id="experiment_3_pep_corpus",
        )

        scorecards = await orchestrator.run(trigger_id)
        record = {
            "pair": pair, "trigger_id": str(trigger_id),
        }

        if not scorecards:
            print("  -> no scoreable candidates")
            record["outcome"] = "NO_CANDIDATES"
            results.append(record)
            tally["NO_CANDIDATES"] = tally.get("NO_CANDIDATES", 0) + 1
            continue

        best = None
        for sc in scorecards:
            if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
                best = sc

        if best is None:
            print(f"  -> {len(scorecards)} candidate(s), none passed Layer 1")
            record["outcome"] = "NO_PASS"
            record["n_candidates"] = len(scorecards)
            results.append(record)
            tally["NO_PASS"] = tally.get("NO_PASS", 0) + 1
            continue

        candidate_row = await pool.fetchrow(
            "SELECT rationale, change_set, no_action_justified FROM candidates WHERE id = $1",
            best.candidate_id,
        )
        classification = classify_resolution(
            candidate_row["change_set"], pair["superseded_id"], pair["superseding_id"],
            no_action_justified=candidate_row["no_action_justified"],
        )
        print(f"  groundedness={best.layer1.groundedness_score:.2f}  classification={classification}")
        print(f"  rationale: {candidate_row['rationale'][:300]}")

        if args.apply and classification == "CORRECT":
            change_set = ChangeSet.model_validate(candidate_row["change_set"])
            updater = KnowledgeUpdater(pool)
            applied = await updater.apply(change_set, approver_id="experiment_3_pep_corpus")
            print(f"  APPLIED: {applied}")
        elif args.apply:
            print(f"  NOT applying -- classification is {classification}, not CORRECT")

        record.update({
            "outcome": "passed", "classification": classification,
            "groundedness": best.layer1.groundedness_score,
            "candidate_id": str(best.candidate_id),
            "rationale": candidate_row["rationale"],
            "change_set": candidate_row["change_set"],
        })
        results.append(record)
        tally[classification] = tally.get(classification, 0) + 1

    await pool.close()

    print(f"\n{'='*70}\nTALLY over {len(usable)} real pairs\n{'='*70}")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    # NO_ACTION_JUSTIFIED_UNVERIFIED deliberately excluded from the
    # direction-correctness denominator, same as NO_PASS/NO_CANDIDATES --
    # there is no update op to check a target id against, so this
    # mechanical check cannot confirm or deny direction for it. It's a
    # distinct, real outcome (a judge-approved "no action needed" on a
    # corpus where every pair IS a genuine supersession) and worth
    # reviewing by hand, not silently averaged into either CORRECT or
    # FALSE_POSITIVE_MISDIAGNOSIS.
    n_scored = sum(v for k, v in tally.items() if k in (
        "CORRECT", "WRONG_DIRECTION", "FALSE_POSITIVE_MISDIAGNOSIS", "OTHER_TARGET", "UNPARSEABLE_CHANGE_SET",
    ))
    if n_scored:
        print(f"\n  correct-direction rate (of {n_scored} scored, excluding NO_PASS/NO_CANDIDATES/"
              f"NO_ACTION_JUSTIFIED_UNVERIFIED): "
              f"{tally.get('CORRECT', 0)}/{n_scored} = {tally.get('CORRECT', 0)/n_scored:.1%}")
    if tally.get("NO_ACTION_JUSTIFIED_UNVERIFIED"):
        print(f"\n  NOTE: {tally['NO_ACTION_JUSTIFIED_UNVERIFIED']} pair(s) passed via a judge-approved "
              f"no-action resolution -- review these by hand, direction cannot be mechanically confirmed.")

    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote {args.out} ({len(results)} result(s)) -- point verify_change_set_grounding.py "
          f"at this file next: python scripts/verify_change_set_grounding.py --results {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
