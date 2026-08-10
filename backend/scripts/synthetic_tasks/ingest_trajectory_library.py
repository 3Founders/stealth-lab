"""
Ingests the real trajectory library: Task A's masked trajectory (the
thing retrieval SHOULD find for Task B) plus a genuinely different
decoy (something retrieval should correctly rank lower). Stored as
knowledge_nodes -- not task_nodes -- deliberately: this makes both the
existing generic hierarchical_search()+postcondition-gate code AND the
existing knowledge_conflict.py debate mechanism directly usable with
zero new plumbing (both already work identically on knowledge_nodes;
confirmed by reading _props_col() and knowledge_conflict.py's SQL
directly, not assumed).

Run from backend/:
    python scripts/synthetic_tasks/ingest_trajectory_library.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder, to_pgvector
from masked_trajectory import TASK_A_MASKED_TRAJECTORY, TASK_DECOY_MASKED_TRAJECTORY

NODES = [
    {
        "key": "trajectory_csv_groupby_aggregate",
        "name": "Solved pattern: CSV group-by, sum+count, exclude by status",
        "content": TASK_A_MASKED_TRAJECTORY,
        "postconditions": ["pattern:groupby_aggregate", "io:csv_to_json"],
    },
    {
        "key": "trajectory_csv_dedupe",
        "name": "Solved pattern: CSV dedupe by key",
        "content": TASK_DECOY_MASKED_TRAJECTORY,
        "postconditions": ["pattern:dedupe_filter", "io:csv_to_json"],
    },
]


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()

    texts = [n["content"] for n in NODES]
    print(f"embedding {len(texts)} trajectory node(s)...")
    vectors = await embedder.embed(texts, input_type="document")

    node_ids = {}
    for node, vec in zip(NODES, vectors):
        row = await pool.fetchrow(
            "INSERT INTO knowledge_nodes (node_type, name, properties, embedding, created_by) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            "solved_trajectory", node["name"],
            json.dumps({"content": node["content"], "postconditions": node["postconditions"]}),
            to_pgvector(vec), "synthetic_task_experiment",
        )
        node_ids[node["key"]] = str(row["id"])
        print(f"  {node['key']} -> {row['id']}")

    await pool.close()

    out_path = os.path.join(os.path.dirname(__file__), "trajectory_library_ids.json")
    with open(out_path, "w") as f:
        json.dump(node_ids, f, indent=2)
    print(f"\nwrote node ids to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
