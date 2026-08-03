"""
Apply the schema and seed the reference workflow.

    python scripts/seed.py                 # apply db/*.sql, then embed tasks
    python scripts/seed.py --schema-only   # apply the SQL, skip embeddings
    python scripts/seed.py --embed-only    # backfill embeddings only
    python scripts/seed.py --status        # report, change nothing

Every .sql file in db/ is idempotent, so this is safe to re-run on every
deploy. Embeddings are backfilled separately because they need a Voyage key
and the SQL does not -- a graph with NULL embeddings is lexically searchable
and otherwise fully functional, which is the right degradation.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db import create_pool, qualified_schema, verify_isolation  # noqa: E402
from app.services.embeddings import Embedder, task_text, to_pgvector  # noqa: E402

DB_DIR = Path(__file__).resolve().parent.parent / "db"


async def preflight(pool) -> int:
    """
    Make the schema exist and confirm pgvector is reachable.

    Both before any DDL runs, because the failures otherwise surface deep in
    01_schema.sql as "type vector does not exist", which points at the file
    rather than at the database it is being applied to.
    """
    schema = qualified_schema()
    async with pool.acquire() as conn:
        server = await conn.fetchval("SHOW server_version")
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        print(f"  Postgres {server}, schema {schema} ready")

        installed = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
        if installed:
            print(f"  pgvector {installed} already installed")
            return 0

        available = await conn.fetchval(
            "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
        )
        if not available:
            print(
                "  pgvector is not installed and not available on this server.\n"
                "  plat_v1 needs it for task retrieval. Options: a managed Postgres "
                "that ships it\n  (Supabase, Neon, RDS), or `docker run -d -e "
                "POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg17`."
            )
            return 1
        print(f"  pgvector {available} available; 01_schema.sql will enable it")
    return 0


async def apply_sql(pool) -> int:
    files = sorted(DB_DIR.glob("*.sql"))
    if not files:
        print(f"no .sql files in {DB_DIR}")
        return 1

    async with pool.acquire() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            try:
                # No transaction wrapper here: each file opens its own with an
                # explicit BEGIN/COMMIT, because psql (which does not default
                # to ON_ERROR_STOP) needs the file to be atomic on its own.
                # Nesting ours around it would make the file's COMMIT close
                # the outer transaction early.
                await conn.execute(sql)
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {path.name}: {type(exc).__name__}: {exc}")
                return 1
            print(f"  [ok]   {path.name}")

        # The tables exist -- but do the names resolve to *ours*? pgvector
        # forces the extension's schema onto the search_path, and on this
        # project that schema is `public`, which is also where backend_v2
        # keeps its own task_nodes and traces.
        await verify_isolation(conn)
        print(f"  [ok]   task_nodes and traces resolve to {qualified_schema()}")
    return 0


async def backfill_embeddings(pool) -> int:
    # Guarded before any unqualified read or write. Every column this touches
    # -- id, name, description, embedding, t_invalid -- also exists on
    # backend_v2's public.task_nodes, so if our table is missing the UPDATE
    # below would not error. It would embed backend_v2's task nodes and
    # overwrite their vectors, quietly and with a Voyage bill attached.
    async with pool.acquire() as conn:
        try:
            await verify_isolation(conn)
        except RuntimeError as exc:
            print(f"  {exc}")
            return 1

    rows = await pool.fetch(
        """
        SELECT id, name, description FROM task_nodes
        WHERE embedding IS NULL AND t_invalid IS NULL
        """
    )
    if not rows:
        print("  every live task already has an embedding")
        return 0

    if not os.environ.get("VOYAGE_API_KEY"):
        print(
            f"  {len(rows)} task(s) have no embedding and VOYAGE_API_KEY is not set.\n"
            f"  They remain searchable lexically; vector search will not see them.\n"
            f"  Set the key and re-run with --embed-only to fix."
        )
        return 0

    embedder = Embedder()
    texts = [task_text(r["name"], r["description"]) for r in rows]
    try:
        # One batched call, not one per task.
        vectors = await embedder.embed(texts, input_type="document")
    except Exception as exc:  # noqa: BLE001
        print(f"  embedding failed: {exc}")
        return 1

    async with pool.acquire() as conn, conn.transaction():
        for row, vector in zip(rows, vectors):
            await conn.execute(
                "UPDATE task_nodes SET embedding = $2::vector WHERE id = $1",
                row["id"],
                to_pgvector(vector),
            )
    print(f"  embedded {len(rows)} task(s)")
    return 0


async def status(pool) -> int:
    # Same reason as backfill_embeddings: an unqualified read of `task_nodes`
    # on an unseeded schema would report backend_v2's rows as though they
    # were ours.
    async with pool.acquire() as conn:
        try:
            await verify_isolation(conn)
        except RuntimeError as exc:
            print(exc)
            return 1

    rows = await pool.fetch(
        """
        SELECT t.name, t.kind,
               COUNT(i.id) FILTER (WHERE i.enabled AND i.t_invalid IS NULL) AS impls,
               (t.embedding IS NOT NULL) AS embedded
        FROM task_nodes t
        LEFT JOIN implementations i ON i.task_node_id = t.id
        WHERE t.t_invalid IS NULL
        GROUP BY t.id ORDER BY t.name
        """
    )
    if not rows:
        print("no live task nodes")
        return 0
    print(f"{'task':<26} {'kind':<10} {'impls':>5}  embedded")
    for row in rows:
        print(
            f"{row['name']:<26} {row['kind']:<10} {row['impls']:>5}  "
            f"{'yes' if row['embedded'] else 'no'}"
        )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--embed-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set (put it in .env or export it)")
        return 1

    # The placeholder from .env.example connects as a role literally called
    # "user", and Postgres reports that as `role "user" does not exist` --
    # which reads like a permissions problem rather than an unedited config
    # file. Catch it here and say what actually happened.
    if "://user:pass@" in dsn:
        print(
            "DATABASE_URL is still the .env.example placeholder "
            "(postgresql://user:pass@localhost:5432/plat_v1).\n"
            "Edit plat_v1/.env and point it at a real Postgres 15+ with pgvector.\n\n"
            "Note: plat_v1 needs its own database or its own schema. It defines\n"
            "task_nodes and traces, and so does backend_v2 -- sharing one database\n"
            "means CREATE TABLE IF NOT EXISTS silently skips and the seed then runs\n"
            "against the wrong table definitions."
        )
        return 1

    pool = await create_pool()
    try:
        if args.status:
            return await status(pool)

        if not args.embed_only:
            print("preflight:")
            code = await preflight(pool)
            if code:
                return code

            print("applying schema and seeds:")
            code = await apply_sql(pool)
            if code:
                return code

        if not args.schema_only:
            print("embeddings:")
            code = await backfill_embeddings(pool)
            if code:
                return code

        print("\ndone. `python scripts/seed.py --status` to see what is registered.")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
