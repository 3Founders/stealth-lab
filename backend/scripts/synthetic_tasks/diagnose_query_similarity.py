"""
Same real diagnostic as diagnose_task_c_similarity.py, generalized to
take any query text directly rather than reading a specific task's
instruction.md -- so we can check exactly what a real MCP tool call
actually saw, not a proxy for it.

Run from backend/:
    python scripts/synthetic_tasks/diagnose_query_similarity.py "your query text here"
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


async def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_query_similarity.py \"your query text\"")
        return 1
    query = sys.argv[1]

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()
    query_vec = await embedder.embed_one(query, input_type="query")

    rows = await pool.fetch(
        "SELECT id, name, 1 - (embedding <=> $1::vector) AS similarity "
        "FROM knowledge_nodes WHERE t_invalid IS NULL AND embedding IS NOT NULL "
        "ORDER BY similarity DESC LIMIT 15",
        to_pgvector(query_vec),
    )
    await pool.close()

    print(f"Query: {query!r}\n")
    print(f"Raw similarity against the real corpus (threshold is 0.70, whether reached or not):\n")
    for r in rows:
        flag = "ABOVE threshold" if r["similarity"] >= 0.70 else "below threshold"
        print(f"  {r['similarity']:.4f}  [{flag}]  {r['name']}  ({r['id']})")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
