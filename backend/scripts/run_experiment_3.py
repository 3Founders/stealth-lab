"""
Experiment 3, full pipeline, against the real database and a real debate
(live LLM calls -- needs either the 4 frontier API keys set
(ANTHROPIC_API_KEY, FIREWORKS_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY),
OR USE_GENERAL_COMPUTE=true with GENERAL_COMPUTE_API_KEY /
GENERAL_COMPUTE_PANEL_MODELS / GENERAL_COMPUTE_JUDGE_MODEL set, OR
USE_LOCAL_MODELS=true -- see app/debate/panel.py's default_panel() for
exactly how this gets decided; this script checks whichever path your
.env is actually configured for, not just the frontier-key path).

Steps this script actually performs:
  1. Seed the OLD policy knowledge_node (skipped if it already exists).
  2. Seed the NEW, conflicting policy knowledge_node.
  3. Run knowledge_conflict.detect_and_create_conflict_trigger -- this is
     the new trigger path, confirms the partial-match band catches the
     conflict and the proxy task_node + CONFLICTS_WITH edges + trigger
     row get created for real, not just against the fake-DB tests.
  4. Run the REAL debate loop (LoopOrchestrator) on that trigger -- real
     panel, real Layer 1 evaluation, real scorecards.
  5. Print exactly what happened: which candidates were proposed, what
     each change_set contains, whether Layer 1 passed, and the final
     debate state.
  6. ONLY with --apply: apply the highest-scoring passing candidate via
     KnowledgeUpdater, then re-query to confirm the old policy no longer
     serves and the new one does -- the actual end-to-end claim under
     test. Dry run by default, same posture as every other script in
     this project (dedup_sweep.py, hierarchy build, etc.) -- a real
     debate producing real API cost shouldn't auto-apply its own output
     without a human looking at what it proposed first.

Run from backend/:
    python scripts/run_experiment_3.py            # seeds + detects + debates, does not apply
    python scripts/run_experiment_3.py --apply     # also applies the winning candidate
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.debate.panel import default_judge, default_layer2_agent, default_panel
from app.services.embeddings import Embedder, to_pgvector
from app.services.knowledge_conflict import detect_and_create_conflict_trigger
from app.services.knowledge_update import KnowledgeUpdater
from app.services.loop import LoopOrchestrator

OLD_POLICY_NAME = "Refunds require the original receipt"
NEW_POLICY_TEXT = "As of the current policy revision, refunds under $50 do not require the original receipt"


async def seed_policy(pool, embedder, name: str, node_type="policy") -> str:
    existing = await pool.fetchrow(
        "SELECT id FROM knowledge_nodes WHERE name = $1 AND t_invalid IS NULL", name
    )
    if existing:
        print(f"  '{name}' already exists ({existing['id']}), reusing it")
        return str(existing["id"])

    vec = await embedder.embed_one(name, input_type="document")
    row = await pool.fetchrow(
        "INSERT INTO knowledge_nodes (node_type, name, properties, provenance, embedding, "
        "t_valid, t_created, created_by) "
        "VALUES ($1, $2, '{}', 'company_ingested', $3::vector, now(), now(), 'experiment_3_seed') "
        "RETURNING id",
        node_type, name, to_pgvector(vec),
    )
    print(f"  seeded '{name}' -> {row['id']}")
    return str(row["id"])


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    from app.config import settings
    if settings.use_general_compute:
        if not settings.general_compute_api_key:
            print("use_general_compute is set but GENERAL_COMPUTE_API_KEY is missing")
            sys.exit(1)
        if not settings.general_compute_panel_models or not settings.general_compute_judge_model:
            print("use_general_compute is set but GENERAL_COMPUTE_PANEL_MODELS / "
                  "GENERAL_COMPUTE_JUDGE_MODEL aren't configured — see app/debate/panel.py "
                  "general_compute_panel()/general_compute_judge() for what's required")
            sys.exit(1)
        print("using General Compute for the panel/judge (use_general_compute=True)")
    elif settings.use_local_models:
        print("using local models for the panel/judge (use_local_models=True)")
    else:
        missing_keys = [k for k in ("ANTHROPIC_API_KEY", "FIREWORKS_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
                         if not os.environ.get(k)]
        if missing_keys:
            print(f"Missing API key(s) for the default frontier-model panel: {missing_keys}")
            print("(or set USE_GENERAL_COMPUTE=true / USE_LOCAL_MODELS=true in .env to use a different provider)")
            sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()

    print("[1/3] seeding conflicting policy pair...")
    old_id = await seed_policy(pool, embedder, OLD_POLICY_NAME)
    new_id = await seed_policy(pool, embedder, NEW_POLICY_TEXT)

    print("\n[2/3] detecting conflict and creating trigger...")
    trigger_id = await detect_and_create_conflict_trigger(pool, new_id)
    if trigger_id is None:
        print("  NO CONFLICT DETECTED — the two policies didn't land in the partial-match band.")
        print("  Check their actual embedding similarity before assuming the mechanism is broken:")
        print("  (real embeddings may separate this pair more or less than the fake-DB tests did)")
        await pool.close()
        sys.exit(1)
    print(f"  trigger created: {trigger_id}")

    print("\n[3/3] running the real debate (live LLM calls, this costs real API usage)...")
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )
    scorecards = await orchestrator.run(trigger_id)

    if not scorecards:
        print("  Debate produced no scoreable candidates. Check debate_events for why:")
        print(f"  SELECT * FROM debate_events WHERE debate_id IN "
              f"(SELECT debate_id FROM triggers WHERE id = '{trigger_id}');")
        await pool.close()
        return

    print(f"\n  {len(scorecards)} candidate(s) evaluated:")
    best = None
    for sc in scorecards:
        print(f"\n  candidate {sc.candidate_id}")
        print(f"    layer1_passed: {sc.layer1.passed}  groundedness: {sc.layer1.groundedness_score:.2f}")
        print(f"    recommendation: {sc.recommendation}")
        if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
            best = sc

    if best is None:
        print("\n  No candidate passed Layer 1 — nothing to apply. This is a real, valid outcome:")
        print("  it means the panel's proposal(s) didn't hold up under groundedness/fallacy review.")
        await pool.close()
        return

    candidate_row = await pool.fetchrow("SELECT change_set FROM candidates WHERE id = $1", best.candidate_id)
    print(f"\n  Best passing candidate's change_set: {candidate_row['change_set']}")

    if not args.apply:
        print("\n(dry run — pass --apply to actually apply this candidate and confirm supersession)")
        await pool.close()
        return

    print("\n[applying] the winning candidate...")
    from app.models.change import ChangeSet
    change_set = ChangeSet.model_validate(candidate_row["change_set"])
    updater = KnowledgeUpdater(pool)
    result = await updater.apply(change_set, approver_id="experiment_3_script")
    print(f"  applied: {result}")

    print("\n[confirming] post-decision state...")
    old_live = await pool.fetchrow("SELECT id FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL", old_id)
    print(f"  old policy still live: {old_live is not None} (expect False)")
    new_rows = await pool.fetch(
        "SELECT id, name FROM knowledge_nodes WHERE t_invalid IS NULL "
        "AND id IN (SELECT source_id FROM edges WHERE target_id = $1 AND edge_type = 'SUPERSEDES')",
        old_id,
    )
    print(f"  node(s) now superseding it: {[(str(r['id']), r['name']) for r in new_rows]}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())