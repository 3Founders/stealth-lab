#!/usr/bin/env python
"""Read-only inspection of a structured-skill ingestion run."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from app.db.session import create_pool


async def inspect(run_id: str, *, summary: bool = False) -> None:
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        rows = await pool.fetch(
            "SELECT ia.path, ia.commit, ia.resource_manifest, ia.dependencies, "
            "p.id AS procedure_row_id, p.procedure_id, p.name, p.goal, p.steps, "
            "p.provenance, p.verification_state, p.domain_payload "
            "FROM ingested_artifacts ia LEFT JOIN procedures p "
            "ON p.id=ia.procedure_row_id WHERE ia.run_id=$1::uuid ORDER BY ia.path",
            run_id,
        )
        payload = [dict(row) for row in rows]
        if summary:
            payload = [{
                "path": row["path"], "commit": row["commit"],
                "procedure_id": row["procedure_id"], "name": row["name"],
                "goal": row["goal"], "steps": row["steps"],
                "provenance": row["provenance"],
                "verification_state": row["verification_state"],
                "resources": len(row["resource_manifest"] or []),
                "dependencies": row["dependencies"],
            } for row in payload]
        print(json.dumps(payload, indent=2, default=str))
    finally:
        await pool.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    asyncio.run(inspect(args.run_id, summary=args.summary))


if __name__ == "__main__":
    main()
