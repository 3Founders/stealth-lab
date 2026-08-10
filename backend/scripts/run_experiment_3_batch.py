"""
Runs the real debate pipeline against a sample of real candidate pairs
from conflict_candidates.json (scan_knowledge_conflicts.py's output).

At full scale, this is DELIBERATELY not "every candidate" -- 166,134
pairs found in the last scan is not something to run debate over; even
a conservative estimate of real API calls per debate (multiple
panelists x multiple rounds x judge x Layer 1) makes that cost/time
prohibitive, and most of those pairs are not real contradictions on
this corpus (confirmed on manual inspection: mostly "related, not
conflicting" reference documents, not versioned policy changes). A
large, honestly-sized, UNBIASED sample (see --count, default 150) is
the actual target for a "full" run -- reportable statistics, not
exhaustive coverage.

Sampling is proper random (stratified by same/different category),
not hand-curated -- the earlier 8-pair pilot's evenly-spaced-by-
similarity selection was fine for a quick look, but a run meant to
produce reportable numbers needs an unbiased sample, not one picked to
maximize how interesting each individual case looks.

Retries with backoff around the debate call itself, not just
embedding: General Compute's actual rate limits are unknown (only
tested at n=8 so far), and a run of 150 debates makes far more calls
than the pilot ever did.

Reports, per pair: did the panel find a real conflict worth resolving,
correctly recognize these as compatible/non-conflicting and decline,
or fail to reach a passing candidate at all -- all three are valid,
reportable outcomes. Dry run by default (never applies).

Run from backend/, with conflict_candidates.json present:
    python scripts/run_experiment_3_batch.py --count 150 --seed 42
    python scripts/run_experiment_3_batch.py --count 150 --seed 42 --apply
"""
import argparse
import asyncio
import json
import os
import random
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


def pick_random_sample(candidates: list[dict], count: int, seed: int) -> list[dict]:
    """
    Proper unbiased sampling, stratified by same-category vs different-
    category so both situations are represented proportionally to their
    real frequency in the corpus (not hand-picked to look interesting --
    that was fine for an 8-pair pilot, not for a run meant to produce
    reportable statistics).
    """
    rng = random.Random(seed)
    same_cat = [c for c in candidates if c["category_a"] and c["category_a"] == c["category_b"]]
    diff_cat = [c for c in candidates if c["category_a"] and c["category_b"] and c["category_a"] != c["category_b"]]
    total = len(same_cat) + len(diff_cat)
    if total == 0:
        return rng.sample(candidates, min(count, len(candidates)))

    n_same = round(count * len(same_cat) / total)
    n_diff = count - n_same
    sample = (
        rng.sample(same_cat, min(n_same, len(same_cat))) +
        rng.sample(diff_cat, min(n_diff, len(diff_cat)))
    )
    rng.shuffle(sample)
    return sample


async def run_debate_with_retry(orchestrator, trigger_id, max_retries=3, base_wait=30):
    for attempt in range(max_retries):
        try:
            return await orchestrator.run(trigger_id)
        except Exception as exc:  # noqa: BLE001
            if attempt == max_retries - 1:
                raise
            wait = base_wait * (attempt + 1)
            print(f"  debate call failed ({exc}), retry {attempt+1}/{max_retries} after {wait}s")
            await asyncio.sleep(wait)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--candidates", default="conflict_candidates.json")
    parser.add_argument("--pace-seconds", type=float, default=3.0,
                         help="pause between debates -- proactive spacing, not just retry-on-failure, "
                              "since General Compute's real rate limits are untested at this scale")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    from app.config import settings
    if not (settings.use_general_compute or settings.use_local_models):
        missing = [k for k in ("ANTHROPIC_API_KEY", "FIREWORKS_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
                   if not os.environ.get(k)]
        if missing:
            print(f"Missing API key(s) for the default frontier-model panel: {missing}")
            print("(or set USE_GENERAL_COMPUTE=true / USE_LOCAL_MODELS=true in .env)")
            sys.exit(1)

    candidates = json.load(open(args.candidates))
    sample = pick_random_sample(candidates, args.count, args.seed)
    print(f"selected {len(sample)} pairs from {len(candidates)} candidates "
          f"(unbiased random, stratified by same/different category, seed={args.seed})\n")

    pool = await create_pool(os.environ["DATABASE_URL"])
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )

    results = []
    for i, pair in enumerate(sample):
        print(f"=== [{i+1}/{len(sample)}] sim={pair['similarity']:.3f} "
              f"({pair['category_a']}/{pair['category_b']}) ===")
        print(f"  A: {pair['name_a']}")
        print(f"  B: {pair['name_b']}")

        trigger_id = await create_conflict_trigger_for_pair(
            pool, pair["id_a"], pair["id_b"], pair["similarity"],
        )
        scorecards = await run_debate_with_retry(orchestrator, trigger_id)

        if not scorecards:
            print("  -> no scoreable candidates (panel produced nothing usable)")
            results.append({"pair": pair, "outcome": "no_candidates", "trigger_id": str(trigger_id)})
            continue

        best = None
        for sc in scorecards:
            if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
                best = sc

        if best is None:
            print(f"  -> {len(scorecards)} candidate(s), none passed Layer 1")
            results.append({"pair": pair, "outcome": "no_pass", "n_candidates": len(scorecards),
                             "trigger_id": str(trigger_id)})
            continue

        print(f"  -> PASSED: groundedness={best.layer1.groundedness_score:.2f}, "
              f"recommendation={best.recommendation}")
        candidate_row = await pool.fetchrow("SELECT change_set FROM candidates WHERE id = $1", best.candidate_id)
        print(f"     change_set: {json.dumps(candidate_row['change_set'])[:300]}")
        results.append({
            "pair": pair, "outcome": "passed",
            "groundedness": best.layer1.groundedness_score,
            "candidate_id": str(best.candidate_id),
            "trigger_id": str(trigger_id),
        })

        if args.apply:
            change_set = ChangeSet.model_validate(candidate_row["change_set"])
            updater = KnowledgeUpdater(pool)
            applied = await updater.apply(change_set, approver_id="experiment_3_batch")
            print(f"     APPLIED: {applied}")
            results[-1]["applied"] = applied
        print()

        if i < len(sample) - 1 and args.pace_seconds > 0:
            await asyncio.sleep(args.pace_seconds)

    await pool.close()

    with open("experiment_3_batch_results.json", "w") as fh:
        json.dump(results, fh, indent=2, default=str)

    n = len(results)
    passed = sum(1 for r in results if r["outcome"] == "passed")
    no_pass = sum(1 for r in results if r["outcome"] == "no_pass")
    no_cand = sum(1 for r in results if r["outcome"] == "no_candidates")
    print(f"\n=== summary ===")
    print(f"  {passed}/{n} passed Layer 1 (panel found and resolved a real conflict)")
    print(f"  {no_pass}/{n} produced candidates but none passed (panel tried, didn't hold up)")
    print(f"  {no_cand}/{n} produced no usable candidates")
    print(f"wrote experiment_3_batch_results.json")


if __name__ == "__main__":
    asyncio.run(main())