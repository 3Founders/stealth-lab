"""
Pick the hierarchy build threshold from the corpus, instead of assuming it.

The default DEFAULT_GROUP_THRESHOLD = 0.75 produced a near-degenerate tree
on this corpus: 5 internal nodes and 17 surviving roots over 22 leaves,
i.e. mostly flat. A hierarchical-search arm run against that tree is not
measuring hierarchical search -- it is measuring flat search with extra
steps, and would report a meaningless null result.

So: dump the actual pairwise similarity distribution among the 22 skill
documents, then dry-run the builder across candidate thresholds and report
the resulting tree shape. The threshold is chosen from that evidence and
written into the manifest, not picked by feel.

Note this only became possible after fixing _pairwise_similarity, which
ignored the caller's `threshold` argument entirely and always used the
module default -- so before that fix, every sweep value produced an
identical tree.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.hierarchy import build_hierarchy_for_table

LIBRARY_CREATED_BY = "after_experiment"
HIERARCHY_CREATED_BY = "hierarchy_builder"


async def clear_hierarchy(pool) -> None:
    """Remove only the synthetic tree, leaving the 22 leaf skills intact."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM edges WHERE created_by = $1", HIERARCHY_CREATED_BY
            )
            await conn.execute(
                "DELETE FROM task_nodes WHERE created_by = $1", HIERARCHY_CREATED_BY
            )


async def similarity_distribution(pool) -> dict:
    rows = await pool.fetch(
        "SELECT 1 - (a.embedding <=> b.embedding) AS sim "
        "FROM task_nodes a JOIN task_nodes b ON a.id < b.id "
        "WHERE a.created_by = $1 AND b.created_by = $1 "
        "AND a.t_invalid IS NULL AND b.t_invalid IS NULL",
        LIBRARY_CREATED_BY,
    )
    sims = sorted(float(r["sim"]) for r in rows)
    if not sims:
        return {}
    def pct(p: float) -> float:
        return round(sims[min(len(sims) - 1, int(len(sims) * p))], 4)
    return {
        "pairs": len(sims),
        "min": round(sims[0], 4), "max": round(sims[-1], 4),
        "mean": round(statistics.mean(sims), 4),
        "median": round(statistics.median(sims), 4),
        "p75": pct(0.75), "p90": pct(0.90), "p95": pct(0.95), "p99": pct(0.99),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", type=float, default=None,
                    help="rebuild the tree for real at this threshold")
    args = ap.parse_args()

    pool = await create_pool(min_size=1, max_size=4)
    scope = AccessScope.unrestricted()
    try:
        leaves = await pool.fetchval(
            "SELECT count(*) FROM task_nodes WHERE created_by = $1 AND t_invalid IS NULL",
            LIBRARY_CREATED_BY,
        )
        print(f"library leaves: {leaves}")
        dist = await similarity_distribution(pool)
        print("pairwise cosine among library skills:")
        print(json.dumps(dist, indent=2))

        if args.apply is None:
            print("\nthreshold sweep (dry run -- tree is cleared before each):")
            print(f"{'thresh':>7} {'internal':>9} {'roots':>6} {'levels':>7}")
            for t in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
                await clear_hierarchy(pool)
                r = await build_hierarchy_for_table(
                    pool, "task_nodes", scope=scope, threshold=t, apply=True,
                )
                roots = await pool.fetchval(
                    "SELECT count(*) FROM task_nodes n WHERE n.t_invalid IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL "
                    "  AND e.edge_type='OWNS' AND e.custom_edge_type='PARENT_OF' "
                    "  AND e.target_id = n.id AND e.target_table='task_nodes')"
                )
                print(f"{t:>7.2f} {r.internal_nodes_created:>9} {roots:>6} {r.levels_built:>7}")
            await clear_hierarchy(pool)
            print("\ntree cleared; re-run with --apply <threshold> to build for real")
            return 0

        await clear_hierarchy(pool)
        r = await build_hierarchy_for_table(
            pool, "task_nodes", scope=scope, threshold=args.apply, apply=True,
        )
        roots = await pool.fetchval(
            "SELECT count(*) FROM task_nodes n WHERE n.t_invalid IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL "
            "  AND e.edge_type='OWNS' AND e.custom_edge_type='PARENT_OF' "
            "  AND e.target_id = n.id AND e.target_table='task_nodes')"
        )
        print(f"\nbuilt at threshold={args.apply}: {r.internal_nodes_created} internal "
              f"nodes, {roots} roots, {r.levels_built} levels")
        mf = Path(__file__).parent / "library_manifest.json"
        manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        manifest.update({
            "hierarchy_threshold": args.apply,
            "internal_nodes": r.internal_nodes_created,
            "roots": roots,
            "levels": r.levels_built,
            "similarity_distribution": dist,
        })
        mf.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("manifest updated")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
