"""
Experiment 5b -- what is the hierarchy's cost saving, actually?

Experiment 5 reported hierarchical search using 99.2 "comparisons" against
flat's 1,555 at the largest corpus, a 15.7x saving. That figure deserves
scrutiny, because only one of the two numbers was measured:

    flat_cmp.append(len(nodes))        # ASSUMED: a linear scan
    hier_cmp.append(res.comparisons)   # MEASURED: from SearchResult

But 01_ontology.sql builds an HNSW index on task_nodes.embedding
(vector_cosine_ops). HNSW is a navigable small-world graph -- approximate
nearest neighbour in roughly log time. If Postgres uses it, flat retrieval
never performs 1,555 comparisons and the headline ratio is measured
against a straw baseline.

This audit answers three questions with evidence rather than assumption:

  1. Does the flat query actually use the HNSW index? (EXPLAIN ANALYZE)
     Note the query as written orders by `1 - (embedding <=> q) DESC`, a
     computed expression. pgvector's index answers `embedding <=> q ASC`.
     Those are not the same plan, and the difference is invisible in the
     result set -- only in the plan.
  2. What does each approach cost in WALL CLOCK and DB ROUND TRIPS, which
     is what a user and an invoice actually experience?
  3. How does that scale from 22 to 1,555 nodes?
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.hierarchy import build_hierarchy_for_table, hierarchical_search
from experiments.after.corpus import load_tasks
from experiments.after.exp5_scale import (
    CREATED_BY, SCALE_DSN, load_library, make_sizes, p90_threshold, reset,
)
from experiments.after.local_embed import LocalEmbedder
from experiments.after.scale_corpus import build_distractors, build_labelled_nodes


class CountingPool:
    """Wraps a pool and counts every round trip the code under test makes."""

    def __init__(self, pool):
        self._pool = pool
        self.calls = 0

    async def fetch(self, *a, **k):
        self.calls += 1
        return await self._pool.fetch(*a, **k)

    async def fetchrow(self, *a, **k):
        self.calls += 1
        return await self._pool.fetchrow(*a, **k)

    async def fetchval(self, *a, **k):
        self.calls += 1
        return await self._pool.fetchval(*a, **k)

    async def execute(self, *a, **k):
        self.calls += 1
        return await self._pool.execute(*a, **k)

    def acquire(self):
        return self._pool.acquire()


EXPR_ORDER = (
    "SELECT name, skill_ref, 1 - (embedding <=> $1::vector) AS sim FROM task_nodes "
    "WHERE created_by = $2 AND t_invalid IS NULL AND embedding IS NOT NULL "
    "ORDER BY sim DESC LIMIT 1"
)
OPERATOR_ORDER = (
    "SELECT name, skill_ref FROM task_nodes "
    "WHERE created_by = $2 AND t_invalid IS NULL AND embedding IS NOT NULL "
    "ORDER BY embedding <=> $1::vector LIMIT 1"
)


async def explain(pool, sql: str, vec: str) -> dict:
    rows = await pool.fetch(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", vec, CREATED_BY)
    raw = rows[0][0]
    # app/db/session.py registers a JSONB codec, so this arrives already
    # decoded on a pooled connection but as a string on a raw one.
    payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    plan = payload[0]
    text = json.dumps(plan)
    return {
        "uses_hnsw_index": "idx_tn_embedding" in text,
        "seq_scan": "Seq Scan" in text,
        "index_scan": "Index Scan" in text,
        "execution_ms": round(plan["Execution Time"], 2),
        "plan_node": plan["Plan"]["Node Type"],
        "actual_rows_scanned": plan["Plan"].get("Actual Rows"),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=SCALE_DSN)
    ap.add_argument("--queries", type=int, default=40)
    args = ap.parse_args()

    tasks = [t for t in load_tasks() if len(t.gold_skills) == 1][: args.queries]
    labelled = build_labelled_nodes()
    distractors = build_distractors()
    sizes = make_sizes(labelled, distractors)
    sizes = {k: sizes[k] for k in ("S1_skills_only", "S4_all_labelled", "S5_with_distractors")}

    embedder = LocalEmbedder()
    all_nodes = {n.key: n for v in sizes.values() for n in v}
    vecs = await embedder.embed([n.text for n in all_nodes.values()], input_type="document")
    vectors = dict(zip(all_nodes.keys(), vecs))
    qvecs = await embedder.embed([t.instruction for t in tasks], input_type="query")

    pool = await create_pool(args.dsn, min_size=2, max_size=8)
    scope = AccessScope.unrestricted()
    out = []
    try:
        for label, nodes in sizes.items():
            print(f"\n=== {label}: {len(nodes)} nodes ===")
            await reset(pool)
            await load_library(pool, nodes, vectors)
            # ANALYZE so the planner has real statistics; without it the
            # plan choice reflects an empty-table estimate.
            await pool.execute("ANALYZE task_nodes")
            thr = await p90_threshold(pool)
            await build_hierarchy_for_table(
                pool, "task_nodes", scope=scope, embedder=embedder, threshold=thr, apply=True)
            await pool.execute("ANALYZE task_nodes")

            v0 = to_pgvector(qvecs[0])
            plans = {
                "expression_order_as_used_in_exp5": await explain(pool, EXPR_ORDER, v0),
                "operator_order_index_friendly": await explain(pool, OPERATOR_ORDER, v0),
            }
            print(json.dumps(plans, indent=2))

            flat_ms, op_ms, hier_ms, hier_trips, hier_cmp = [], [], [], [], []
            for t, q in zip(tasks, qvecs):
                vec = to_pgvector(q)
                t0 = time.perf_counter()
                await pool.fetch(EXPR_ORDER, vec, CREATED_BY)
                flat_ms.append((time.perf_counter() - t0) * 1000)

                t0 = time.perf_counter()
                await pool.fetch(OPERATOR_ORDER, vec, CREATED_BY)
                op_ms.append((time.perf_counter() - t0) * 1000)

                counting = CountingPool(pool)
                t0 = time.perf_counter()
                res = await hierarchical_search(
                    counting, "task_nodes", t.instruction, scope=scope,
                    embedder=embedder, beam=3, adaptive=True)
                hier_ms.append((time.perf_counter() - t0) * 1000)
                hier_trips.append(counting.calls)
                hier_cmp.append(res.comparisons)

            row = {
                "corpus": label,
                "nodes": len(nodes),
                "plans": plans,
                "flat_expr_ms_median": round(statistics.median(flat_ms), 2),
                "flat_operator_ms_median": round(statistics.median(op_ms), 2),
                "hier_ms_median": round(statistics.median(hier_ms), 2),
                "hier_db_round_trips_mean": round(statistics.mean(hier_trips), 1),
                "flat_db_round_trips": 1,
                "hier_reported_comparisons_mean": round(statistics.mean(hier_cmp), 1),
                "speed_ratio_hier_over_flat_expr": round(
                    statistics.median(hier_ms) / statistics.median(flat_ms), 2),
            }
            out.append(row)
            print(f"  flat(expr)  median {row['flat_expr_ms_median']} ms, 1 round trip")
            print(f"  flat(op)    median {row['flat_operator_ms_median']} ms, 1 round trip")
            print(f"  hierarchical median {row['hier_ms_median']} ms, "
                  f"{row['hier_db_round_trips_mean']} round trips")

        await reset(pool)
        Path(__file__).parent.joinpath("results_exp5b_cost_audit.json").write_text(
            json.dumps({"queries": len(tasks), "rows": out}, indent=2), encoding="utf-8")

        print("\n=== SUMMARY: real cost, not assumed comparisons ===")
        print(f"{'corpus':<22}{'nodes':>7}{'flat ms':>10}{'hier ms':>10}"
              f"{'hier trips':>12}{'hier/flat':>11}")
        for r in out:
            print(f"{r['corpus']:<22}{r['nodes']:>7}{r['flat_expr_ms_median']:>10.2f}"
                  f"{r['hier_ms_median']:>10.2f}{r['hier_db_round_trips_mean']:>12.1f}"
                  f"{r['speed_ratio_hier_over_flat_expr']:>11.2f}x")
        print("\nwrote results_exp5b_cost_audit.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
