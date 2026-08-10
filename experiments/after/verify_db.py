"""
Gate check: everything the AFTER experiments depend on, verified against
the real local Postgres before any expensive run starts.

Exists because the failure mode this project actually has is silent
degradation, not loud errors -- every retrieval call site catches
embedding/vector failures and falls back to lexical. A run that starts on
a half-working database produces plausible numbers measuring the wrong
mechanism. This asserts loudly instead.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg

from app.config import settings


async def main() -> int:
    problems: list[str] = []
    dsn = settings.database_url
    if not dsn:
        print("FAIL: DATABASE_URL is not set")
        return 1

    host = dsn.split("@")[-1]
    print(f"target: {host}")
    if "supabase" in host:
        print("FAIL: DATABASE_URL still points at Supabase; refusing to run experiments there")
        return 1

    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        print(f"pgvector: {version}")
        if not version:
            problems.append("pgvector extension is not installed")

        # hierarchy.py:217 builds internal nodes with avg(embedding).
        # pgvector added vector avg() in 0.5.0; confirm on THIS server.
        await conn.execute("CREATE TEMP TABLE _v (e vector(3))")
        await conn.execute("INSERT INTO _v VALUES ('[1,0,0]'), ('[0,1,0]')")
        avg = await conn.fetchval("SELECT avg(e)::text FROM _v")
        print(f"avg(vector): {avg}")
        if avg is None:
            problems.append("avg(vector) is unsupported -- build_hierarchy_for_table cannot work")

        dist = await conn.fetchval("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector")
        print(f"cosine distance operator: {dist}")

        # unnest(...) CROSS JOIN with a ::vector cast is the batched query
        # shape in hierarchy.py's batch_hierarchical_search.
        rows = await conn.fetch(
            "SELECT q.ref, 1 - (v.e <=> q.vec::vector) AS sim "
            "FROM unnest($1::text[], $2::text[]) AS q(ref, vec) CROSS JOIN _v v",
            ["a", "b"], ["[1,0,0]", "[0,1,0]"],
        )
        print(f"batched unnest/cast query: {len(rows)} rows")
        if len(rows) != 4:
            problems.append(f"batched query shape returned {len(rows)} rows, expected 4")

        expected = {
            "task_nodes", "knowledge_nodes", "edges", "traces", "triggers",
            "debates", "candidates", "scorecards", "decompositions", "agents",
        }
        present = {
            r["table_name"] for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        missing = sorted(expected - present)
        print(f"tables: {len(present)} present, {len(missing)} of the required set missing")
        if missing:
            problems.append(f"missing tables: {missing}")

        dim = await conn.fetchval(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname='task_nodes' AND a.attname='embedding'"
        )
        print(f"task_nodes.embedding dimension: {dim}")
        if dim != settings.embedding_dimension:
            problems.append(
                f"column dimension {dim} != settings.embedding_dimension "
                f"{settings.embedding_dimension}"
            )
    finally:
        await conn.close()

    if problems:
        print("\n*** GATE FAILED ***")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nGATE PASSED -- database is ready for the experiments")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
