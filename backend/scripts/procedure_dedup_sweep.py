"""Dry-run/apply deduplication for canonical Procedures."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import Embedder
from app.services.procedures import run_procedure_dedup_sweep


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set")
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        reports = await run_procedure_dedup_sweep(
            pool, scope=AccessScope.unrestricted(), embedder=Embedder(), apply=args.apply,
        )
        print({"clusters": len(reports), "apply": args.apply, "reports": reports})
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
