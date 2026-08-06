"""
Reuse consolidation batch sweep (Part A, app/services/dedup.py).

Finds duplicate task_nodes/knowledge_nodes clusters using complete-
linkage clustering over real cosine similarity (or lexical overlap as
fallback when embeddings aren't present), and -- only with --apply --
merges them: earliest-created node in each cluster stays canonical,
the rest are invalidated and their edges rewired onto it.

Supersedes hide_duplicate_seeds.py, which only matched by exact name
and hid duplicates (visibility='private') rather than properly merging
them via the bi-temporal model.

Usage (from backend/, with a populated .env):
    python scripts/dedup_sweep.py            # dry run, prints clusters found
    python scripts/dedup_sweep.py --apply     # actually merges them
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.dedup import run_dedup_sweep
from app.services.embeddings import Embedder


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually merge (default: dry run)")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    reports = await run_dedup_sweep(
        pool,
        scope=AccessScope.unrestricted(),
        embedder=Embedder(),
        apply=args.apply,
    )
    await pool.close()

    if not reports:
        print("No duplicate clusters found.")
        return

    mode = "MERGED" if args.apply else "WOULD MERGE (dry run -- pass --apply to write)"
    print(f"{mode}: {len(reports)} cluster(s)\n")
    for r in reports:
        print(f"  [{r.table}] keep {r.canonical_name!r} ({r.canonical_id})")
        for name, id_ in zip(r.merged_names, r.merged_ids):
            print(f"      merges: {name!r} ({id_})")
        if r.rewired_edges >= 0:
            print(f"      rewired {r.rewired_edges} edge(s)")
        print()

    if not args.apply:
        print("Nothing written. Re-run with --apply to merge these clusters.")


if __name__ == "__main__":
    asyncio.run(main())
