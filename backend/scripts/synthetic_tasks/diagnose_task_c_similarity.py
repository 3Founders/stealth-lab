"""
Diagnostic: find_reusable_nodes() silently drops anything below
PARTIAL_MATCH_THRESHOLD (0.70) without reporting the actual score --
useful for production, not for understanding a real "0 candidates"
result. This queries the raw similarity directly so we know whether
Task C missed by a hair or was genuinely dissimilar.

Run from backend/:
    python scripts/synthetic_tasks/diagnose_task_c_similarity.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder, to_pgvector

HERE = Path(__file__).parent


async def main():
    instruction = (HERE / "task_c" / "instruction.md").read_text()

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()
    query_vec = await embedder.embed_one(instruction, input_type="query")

    rows = await pool.fetch(
        "SELECT id, name, 1 - (embedding <=> $1::vector) AS similarity "
        "FROM knowledge_nodes WHERE t_invalid IS NULL AND embedding IS NOT NULL "
        "ORDER BY similarity DESC",
        to_pgvector(query_vec),
    )
    await pool.close()

    print(f"Raw similarity for Task C's instruction against every real knowledge_node "
          f"(threshold is 0.70, whether reached or not):\n")
    for r in rows:
        flag = "ABOVE threshold" if r["similarity"] >= 0.70 else "below threshold"
        print(f"  {r['similarity']:.4f}  [{flag}]  {r['name']}  ({r['id']})")


if __name__ == "__main__":
    asyncio.run(main())
