"""
Real retrieval step: embeds Task B's instruction and calls
find_reusable_nodes() -- the actual, already-tested reuse-detection
function (reuse_detection.py), not a bespoke query written for this
experiment. Confirms which trajectory (Task A's real pattern, or the
decoy) actually wins on real embedding similarity, rather than
continuing to hand-pick Task A the way earlier versions of this
experiment did.

Requires ingest_trajectory_library.py to have been run first.

Run from backend/:
    python scripts/synthetic_tasks/retrieve_trajectory.py
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
from app.services.reuse_detection import find_reusable_nodes

HERE = Path(__file__).parent


async def retrieve_for_task_b() -> tuple[str, str] | tuple[None, None]:
    """Returns (retrieved_trajectory_text, retrieved_node_id), or
    (None, None) if nothing cleared the real match threshold -- a
    real, valid possible outcome, not an error."""
    instruction = (HERE / "task_b" / "instruction.md").read_text()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    candidates = await find_reusable_nodes(pool, problem=instruction)

    print(f"real retrieval returned {len(candidates)} candidate(s) above match threshold:")
    for c in candidates:
        print(f"  similarity={c.similarity:.4f}  table={c.table}  name={c.name!r}  id={c.id}")

    if not candidates:
        await pool.close()
        print("\nNo candidate cleared the real similarity threshold -- retrieval found nothing "
              "usable. This is a real, valid outcome, not a bug.")
        return None, None

    winner = candidates[0]
    row = await pool.fetchrow(
        f"SELECT properties->>'content' AS content FROM knowledge_nodes WHERE id = $1", winner.id,
    )
    await pool.close()

    print(f"\nWINNER: {winner.name} (similarity={winner.similarity:.4f})")
    return row["content"], winner.id


async def main():
    content, node_id = await retrieve_for_task_b()
    if content is None:
        return 1
    print(f"\nretrieved trajectory content ({len(content)} chars) ready for execution")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
