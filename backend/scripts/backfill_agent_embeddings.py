"""
Backfill embeddings for agents created before vector search was wired
in for the Agent Store (or created via any path that skipped it, e.g.
a direct SQL seed).

Usage (from backend/, with a populated .env):
    python scripts/backfill_agent_embeddings.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder, node_text, to_pgvector

BATCH = 64


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found -- set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()
    print(f"Backfilling agent embeddings with {embedder.model} (dimension {embedder.dimension})")

    rows = await pool.fetch(
        "SELECT id, name, description FROM agents "
        "WHERE embedding IS NULL AND t_invalid IS NULL"
    )
    if not rows:
        print("Nothing to backfill.")
        await pool.close()
        return

    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        texts = [node_text(r["name"], r["description"]) for r in chunk]
        vectors = await embedder.embed(texts, input_type="document")
        async with pool.acquire() as conn:
            for row, vector in zip(chunk, vectors):
                await conn.execute(
                    "UPDATE agents SET embedding = $2::vector WHERE id = $1",
                    row["id"], to_pgvector(vector),
                )
        done += len(chunk)
        print(f"  {done}/{len(rows)}")

    await pool.close()
    print(f"\nDone -- {done} agent(s) embedded. Vector search over the Agent Store is now active.")


if __name__ == "__main__":
    asyncio.run(main())
