"""
Experiment 5 -- does hierarchical retrieval ever beat flat, as scale grows?

Hypothesis A's negative result (p@1 0.528 vs 0.787, and MORE comparisons)
was measured at 22 leaves, where a tree cannot help: there is nothing to
prune, and descent still pays to score internal nodes on the way down. The
defensible claim was never "trees lose", it was "trees lose at 22". This
sweeps the corpus from 22 to 1555 nodes and looks for a crossover in
either accuracy or comparison count.

Everything is measured through the REAL code paths -- hierarchical_search
and build_hierarchy_for_table against live Postgres -- not a
reimplementation, so a crossover found here is a property of the shipped
retriever.

Threshold selection is a rule fixed in advance, not tuned per size: the
grouping threshold is the p90 of that corpus's own pairwise cosine
distribution. Choosing per-size by whichever value made the tree look best
would manufacture the crossover this experiment is meant to detect.

Correctness rule (identical to Hypothesis A, applied at chunk
granularity): a retrieval is correct when the retrieved node's parent
skill equals the task's gold skill. Distractors carry no parent and can
never be correct.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scipy.stats import binomtest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.hierarchy import build_hierarchy_for_table, hierarchical_search
from experiments.after.corpus import load_tasks
from experiments.after.local_embed import LocalEmbedder
from experiments.after.scale_corpus import Node, build_distractors, build_labelled_nodes

CREATED_BY = "exp5_scale"
HIER_BY = "hierarchy_builder"
SCALE_DSN = "postgresql://postgres:stealthlab@127.0.0.1:5433/stealthlab_scale"


def make_sizes(labelled: list[Node], distractors: list[Node], seed: int = 7) -> dict[str, list[Node]]:
    rng = random.Random(seed)
    skills = [n for n in labelled if n.kind == "skill"]
    chunks = [n for n in labelled if n.kind == "chunk"]
    aux = [n for n in labelled if n.kind == "aux"]

    aux_small = aux[:]
    rng.shuffle(aux_small)

    return {
        "S1_skills_only": skills,
        "S2_plus_chunks": skills + chunks,
        "S3_plus_some_aux": skills + chunks + aux_small[:265],
        "S4_all_labelled": skills + chunks + aux,
        "S5_with_distractors": skills + chunks + aux + distractors,
    }


async def reset(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM edges WHERE created_by = ANY($1::text[])", [CREATED_BY, HIER_BY])
            await conn.execute(
                "DELETE FROM task_nodes WHERE created_by = ANY($1::text[])", [CREATED_BY, HIER_BY])


async def load_library(pool, nodes: list[Node], vectors: dict[str, list[float]]) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            for n in nodes:
                await conn.execute(
                    "INSERT INTO task_nodes (name, description, skill_ref, provenance, "
                    "created_by, embedding) VALUES ($1,$2,$3,'company_ingested',$4,$5::vector)",
                    n.name[:400], n.text, n.skill, CREATED_BY, to_pgvector(vectors[n.key]),
                )


async def p90_threshold(pool, sample: int = 400) -> float:
    """p90 of the corpus's own pairwise cosine, computed on a bounded
    random sample -- a full self-join is O(n^2) and at 1555 nodes that is
    1.2M pairs for a number only needed to two decimals."""
    rows = await pool.fetch(
        "WITH s AS (SELECT id, embedding FROM task_nodes WHERE created_by = $1 "
        "AND t_invalid IS NULL ORDER BY random() LIMIT $2) "
        "SELECT 1 - (a.embedding <=> b.embedding) AS sim FROM s a JOIN s b ON a.id < b.id",
        CREATED_BY, sample,
    )
    sims = sorted(float(r["sim"]) for r in rows)
    return round(sims[int(len(sims) * 0.90)], 3) if sims else 0.75


async def flat_query(pool, qvec, k: int = 5) -> list[tuple[str, str | None]]:
    rows = await pool.fetch(
        "SELECT name, skill_ref, 1 - (embedding <=> $1::vector) AS sim FROM task_nodes "
        "WHERE created_by = $2 AND t_invalid IS NULL AND embedding IS NOT NULL "
        "ORDER BY sim DESC LIMIT $3",
        to_pgvector(qvec), CREATED_BY, k,
    )
    return [(r["name"], r["skill_ref"]) for r in rows]


async def run_size(pool, label: str, nodes: list[Node], vectors, tasks, scope, embedder) -> dict:
    await reset(pool)
    t0 = time.time()
    await load_library(pool, nodes, vectors)
    load_s = time.time() - t0

    thr = await p90_threshold(pool)
    t0 = time.time()
    report = await build_hierarchy_for_table(
        pool, "task_nodes", scope=scope, embedder=embedder, threshold=thr, apply=True)
    build_s = time.time() - t0
    roots = await pool.fetchval(
        "SELECT count(*) FROM task_nodes n WHERE n.t_invalid IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM edges e WHERE e.t_invalid IS NULL AND e.edge_type='OWNS' "
        " AND e.custom_edge_type='PARENT_OF' AND e.target_id=n.id AND e.target_table='task_nodes')")

    flat_hits, hier_hits, hier_abort = 0, 0, 0
    flat_cmp, hier_cmp = [], []
    flat_hit_map, hier_hit_map = {}, {}
    n_live = await pool.fetchval(
        "SELECT count(*) FROM task_nodes WHERE t_invalid IS NULL")

    for t in tasks:
        gold = t.gold_skills[0]
        qvec = await embedder.embed_one(t.instruction, input_type="query")

        top = await flat_query(pool, qvec, k=1)
        fh = bool(top) and top[0][1] == gold
        flat_hits += fh
        flat_cmp.append(len(nodes))
        flat_hit_map[t.task_id] = fh

        res = await hierarchical_search(
            pool, "task_nodes", t.instruction, scope=scope, embedder=embedder,
            beam=3, adaptive=True)
        hh = False
        if res.leaf_id and not res.used_flat_fallback:
            row = await pool.fetchrow("SELECT skill_ref FROM task_nodes WHERE id=$1", res.leaf_id)
            hh = bool(row) and row["skill_ref"] == gold
        else:
            hier_abort += 1
        hier_hits += hh
        hier_cmp.append(res.comparisons)
        hier_hit_map[t.task_id] = hh

    b = sum(1 for k in flat_hit_map if flat_hit_map[k] and not hier_hit_map[k])
    c = sum(1 for k in flat_hit_map if hier_hit_map[k] and not flat_hit_map[k])
    p = float(binomtest(b, b + c, 0.5).pvalue) if (b + c) else 1.0

    return {
        "label": label,
        "library_nodes": len(nodes),
        "live_nodes_incl_internal": n_live,
        "distractors": sum(1 for n in nodes if n.kind == "distractor"),
        "group_threshold_p90": thr,
        "internal_nodes": report.internal_nodes_created,
        "levels": report.levels_built,
        "roots": roots,
        "load_seconds": round(load_s, 1),
        "build_seconds": round(build_s, 1),
        "flat_p@1": round(flat_hits / len(tasks), 4),
        "hier_p@1": round(hier_hits / len(tasks), 4),
        "hier_aborted": hier_abort,
        "flat_mean_comparisons": round(statistics.mean(flat_cmp), 1),
        "hier_mean_comparisons": round(statistics.mean(hier_cmp), 1),
        "comparison_ratio_hier_over_flat": round(
            statistics.mean(hier_cmp) / statistics.mean(flat_cmp), 3),
        "mcnemar_flat_vs_hier_p": round(p, 6),
        "flat_wins": b, "hier_wins": c,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", nargs="*", default=None)
    # Separate database, not a separate created_by tag. build_hierarchy_for_table
    # groups whatever is currently rootless in task_nodes, with no filter by
    # creator -- so running this in the main DB would sweep the Experiment 1
    # library into Experiment 5's tree and destroy both.
    ap.add_argument("--dsn", default=SCALE_DSN)
    args = ap.parse_args()

    tasks = [t for t in load_tasks() if len(t.gold_skills) == 1]
    labelled = build_labelled_nodes()
    distractors = build_distractors()
    sizes = make_sizes(labelled, distractors)
    if args.sizes:
        sizes = {k: v for k, v in sizes.items() if k in args.sizes}

    print(f"{len(tasks)} single-skill queries")
    for k, v in sizes.items():
        print(f"  {k}: {len(v)} nodes")

    embedder = LocalEmbedder()
    all_nodes = {n.key: n for v in sizes.values() for n in v}
    print(f"\nembedding {len(all_nodes)} unique nodes + {len(tasks)} queries (local, cached)...")
    t0 = time.time()
    keys = list(all_nodes)
    vecs = await embedder.embed([all_nodes[k].text for k in keys], input_type="document")
    vectors = dict(zip(keys, vecs))
    await embedder.embed([t.instruction for t in tasks], input_type="query")
    print(f"embedded in {time.time()-t0:.0f}s  stats={embedder.stats()}")

    print(f"target DB: {args.dsn.split('@')[-1]}")
    pool = await create_pool(args.dsn, min_size=2, max_size=8)
    scope = AccessScope.unrestricted()
    results = []
    try:
        for label, nodes in sizes.items():
            print(f"\n=== {label}: {len(nodes)} nodes ===")
            r = await run_size(pool, label, nodes, vectors, tasks, scope, embedder)
            results.append(r)
            print(json.dumps(r, indent=2))
        await reset(pool)

        out = {"queries": len(tasks), "sweep": results}
        Path(__file__).parent.joinpath("results_exp5_scale.json").write_text(
            json.dumps(out, indent=2), encoding="utf-8")
        print("\n=== SUMMARY ===")
        print(f"{'corpus':<22}{'nodes':>7}{'flat p@1':>10}{'hier p@1':>10}"
              f"{'flat cmp':>10}{'hier cmp':>10}{'p':>10}")
        for r in results:
            print(f"{r['label']:<22}{r['library_nodes']:>7}{r['flat_p@1']:>10.3f}"
                  f"{r['hier_p@1']:>10.3f}{r['flat_mean_comparisons']:>10.1f}"
                  f"{r['hier_mean_comparisons']:>10.1f}{r['mcnemar_flat_vs_hier_p']:>10.5f}")
        print("\nwrote results_exp5_scale.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
