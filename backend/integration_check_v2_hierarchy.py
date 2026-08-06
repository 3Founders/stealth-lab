"""
Integration check for Part B (app/services/hierarchy.py) against a real
database. Not a pytest suite -- like the other integration_check_v2_*.py
scripts in this repo, this exercises real HTTP-adjacent behavior (real
SQL, real embeddings if configured) rather than mocks, because that's
where the genuinely risky assumptions in this module live:

  - pgvector's avg() aggregate actually working as assumed
    (_create_internal_node, attach_new_leaf's ancestor-update query) --
    requires pgvector >= 0.5.0. This is the single biggest unverified
    assumption in Part B; if this script fails at construction, check
    `SELECT extversion FROM pg_extension WHERE extname='vector'` first.
  - The SET-clause correlated subquery in attach_new_leaf's ancestor
    update actually executing (a FROM-clause version of this does NOT
    work in Postgres -- see the comment in hierarchy.py).
  - complete_linkage_clusters (from dedup.py) behaving the same way
    against real, not synthetic, embedding geometry.

Usage (from backend/, with a populated .env, against a database that
already has SOME embedded task_nodes -- e.g. after bootstrap_demo.py
and backfill_embeddings.py have run):

    python integration_check_v2_hierarchy.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import Embedder
from app.services.hierarchy import (
    attach_new_leaf,
    build_hierarchy_for_table,
    hierarchical_search,
)


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    scope = AccessScope.unrestricted()
    embedder = Embedder()

    print("== pgvector avg() sanity check ==")
    try:
        row = await pool.fetchrow(
            "SELECT avg(embedding) AS a FROM task_nodes WHERE embedding IS NOT NULL LIMIT 1"
        )
        print("  avg(vector) executed without error." if row is not None else "  no embedded rows to test against yet.")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        print("  This blocks the whole module -- pgvector's avg() aggregate is required.")
        print("  Check: SELECT extversion FROM pg_extension WHERE extname='vector';")
        await pool.close()
        sys.exit(1)

    for table in ("task_nodes", "knowledge_nodes"):
        print(f"\n== dry-run build_hierarchy_for_table({table}) ==")
        report = await build_hierarchy_for_table(pool, table, scope=scope, embedder=embedder, apply=False)
        print(f"  levels examined: {report.levels_built}, would-create internal nodes: "
              f"{sum(d['internal_nodes_proposed'] for d in report.level_details)}")
        for d in report.level_details:
            print(f"    level {d['level']}: {d['roots_seen']} roots -> {d['groups_formed']} groups "
                  f"({d['internal_nodes_proposed']} internal)")

    apply = "--apply" in sys.argv
    if apply:
        print("\n== APPLYING build for task_nodes ==")
        report = await build_hierarchy_for_table(pool, "task_nodes", scope=scope, embedder=embedder, apply=True)
        print(f"  built {report.internal_nodes_created} internal node(s), "
              f"{report.final_root_count} root(s) remain")

        print("\n== hierarchical_search sanity check ==")
        row = await pool.fetchrow(
            "SELECT name FROM task_nodes WHERE t_invalid IS NULL AND embedding IS NOT NULL LIMIT 1"
        )
        if row:
            result = await hierarchical_search(pool, "task_nodes", row["name"], scope=scope, embedder=embedder, beam=3)
            print(f"  query={row['name']!r} -> leaf={result.leaf_name!r} "
                  f"sim={result.similarity} comparisons={result.comparisons} "
                  f"flat_fallback={result.used_flat_fallback}")
            print("  (searching for a node's own exact name should return itself with similarity ~1.0 --")
            print("   if it doesn't, something in the descent logic is wrong, not just imprecise)")
    else:
        print("\n(dry run only -- pass --apply to actually build the tree and run traversal checks)")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
