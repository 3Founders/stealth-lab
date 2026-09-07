"""
Gate 2B ops fix (one-off, hand-run): the first capture of
mcp-lazy-tool-schema-loading landed with embedding=None, so it could never
rank in real similarity search (the real rehearsal matched a different,
embedded corpus procedure instead). This supersede's that row through the
REAL versioning path -- supersede_procedure(), the exact mechanism any
other source-content change uses -- carrying every column forward and
overriding ONLY the embedding, computed with the real Embedder at storage
time (submit_procedure's own convention). Version 2, same procedure_id;
version 1 tombstoned, never deleted.

Usage (from backend/):
    python scripts/supersede_mcp_lazy_schema_embedding.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.procedures import supersede_procedure

# The v1 row id printed by the original capture run.
PRIOR_ROW_ID = os.environ.get(
    "GATE2B_PRIOR_ROW_ID", "01a07abb-c293-773d-9081-d102f74cb952"
)


async def main() -> None:
    pool = await create_pool()
    try:
        embedding = await Embedder().embed_one(
            "Make an MCP-style tool server's default tool listing lazy-load "
            "tool schemas: tools/list returns lightweight metadata only "
            "(name, description), a targeted mechanism retrieves each tool's "
            "full JSON schema on demand, and ordinary tool invocation is "
            "unchanged",
            input_type="document",
        )
        result = await supersede_procedure(
            pool,
            prior_row_id=PRIOR_ROW_ID,
            changed_fields={"embedding": embedding},
            superseded_by="capture_mcp_lazy_tool_schema_loading",
            reason=(
                "gate-2b: v1 was captured without a goal embedding and could "
                "not rank in similarity search; v2 carries the real "
                "storage-time embedding, everything else unchanged"
            ),
        )
        if result is None:
            print("no live row for that id -- already superseded (valid no-op)")
            return
        print("superseded -> new version row:")
        print(f"  id           = {result['id']}")
        print(f"  procedure_id = {result['procedure_id']}")
        print(f"  version      = {result['version']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
