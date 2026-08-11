"""
Stage 2 of the design: Task B's real, verified winning trajectory
(masked, honest quality note included) gets proposed as a NEW
candidate knowledge_node, and the REAL debate mechanism decides how it
relates to Task A's existing stored trajectory -- keep both as
distinct patterns, merge them into one improved pattern, or supersede.

Reuses the exact same real machinery Experiment 3 already validated on
32 real PEP pairs: create_conflict_trigger_for_pair + LoopOrchestrator
+ default_panel/judge -- no new debate infrastructure written for this.

Requires trajectory_library_ids.json (from ingest_trajectory_library.py).

Run from backend/:
    python scripts/synthetic_tasks/debate_task_a_vs_task_b.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.debate.panel import default_judge, default_layer2_agent, default_panel
from app.services.embeddings import Embedder, to_pgvector
from app.services.knowledge_conflict import create_conflict_trigger_for_pair
from app.services.loop import LoopOrchestrator
from masked_trajectory import TASK_B_MASKED_TRAJECTORY
from verify_quote_grounding import check_quotes_grounded, summarize_check

HERE = Path(__file__).parent


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    library_ids_path = HERE / "trajectory_library_ids.json"
    if not library_ids_path.exists():
        print(f"{library_ids_path} not found -- run ingest_trajectory_library.py first")
        sys.exit(1)
    library_ids = json.loads(library_ids_path.read_text())
    task_a_id = library_ids["trajectory_csv_groupby_aggregate"]

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()

    print("[1/4] embedding Task B's real winning trajectory...")
    vec = (await embedder.embed([TASK_B_MASKED_TRAJECTORY], input_type="document"))[0]

    print("[2/4] inserting Task B's trajectory as a new candidate knowledge_node...")
    row = await pool.fetchrow(
        "INSERT INTO knowledge_nodes (node_type, name, properties, embedding, created_by) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        "solved_trajectory", "Solved pattern: CSV group-by (Task B instance)",
        {"content": TASK_B_MASKED_TRAJECTORY,
         "postconditions": ["pattern:groupby_aggregate", "io:csv_to_json"]},
        to_pgvector(vec), "synthetic_task_experiment",
    )
    task_b_id = str(row["id"])
    print(f"  Task B trajectory node -> {task_b_id}")

    print("[3/4] computing real similarity between Task A's and Task B's trajectories...")
    sim_row = await pool.fetchrow(
        "SELECT 1 - (a.embedding <=> b.embedding) AS similarity "
        "FROM knowledge_nodes a, knowledge_nodes b WHERE a.id = $1 AND b.id = $2",
        task_a_id, task_b_id,
    )
    similarity = float(sim_row["similarity"])
    print(f"  real similarity: {similarity:.4f}")

    print("\n[4/4] running the real debate between Task A's and Task B's trajectories...")
    trigger_id = await create_conflict_trigger_for_pair(
        pool, task_a_id, task_b_id, similarity, approver_id="synthetic_task_debate_update",
    )
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )
    scorecards = await orchestrator.run(trigger_id)

    if not scorecards:
        print("\nNO CANDIDATES -- the debate produced nothing scoreable")
        await pool.close()
        return 1

    best = None
    for sc in scorecards:
        if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
            best = sc

    if best is None:
        print(f"\n{len(scorecards)} candidate(s), NONE passed Layer 1")
        await pool.close()
        return 1

    candidate_row = await pool.fetchrow(
        "SELECT rationale, change_set FROM candidates WHERE id = $1", best.candidate_id,
    )
    print(f"\nDEBATE OUTCOME: groundedness={best.layer1.groundedness_score:.2f}")
    print(f"rationale: {candidate_row['rationale'][:500]}")
    print(f"\nchange_set: {json.dumps(candidate_row['change_set'], indent=2)[:1000]}")

    # Mechanical quote-grounding check -- real, necessary given two
    # separate real runs where the panel confidently claimed a content
    # difference that was false when checked against actual text. A
    # paraphrase can't be verified this way; a quote can.
    print(f"\n{'=' * 70}\nQUOTE GROUNDING CHECK\n{'=' * 70}")
    task_a_content = await pool.fetchval(
        "SELECT properties->>'content' FROM knowledge_nodes WHERE id = $1", task_a_id)
    node_texts = {
        task_a_id: "Solved pattern: CSV group-by, sum+count, exclude by status\n\n" + task_a_content,
        task_b_id: "Solved pattern: CSV group-by (Task B instance)\n\n" + TASK_B_MASKED_TRAJECTORY,
    }
    quote_results = check_quotes_grounded(candidate_row["rationale"], node_texts)
    print(summarize_check(quote_results))

    await pool.close()

    out = {
        "task_a_id": task_a_id, "task_b_id": task_b_id, "trigger_id": str(trigger_id),
        "similarity": similarity, "groundedness": best.layer1.groundedness_score,
        "rationale": candidate_row["rationale"], "change_set": candidate_row["change_set"],
        "quote_grounding_check": quote_results,
    }
    (HERE / "debate_update_results.json").write_text(json.dumps(out, indent=2))
    print(f"\nfull results saved to {HERE / 'debate_update_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
