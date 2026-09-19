"""Scale benchmark for the global search projections (docs/retrieval_architecture.md, "Scale").

    python scripts/benchmark_projection_scale.py --goals 100000 --procedures 20000 --queries 200

Runs against DATABASE_URL (use a DISPOSABLE database: it loads synthetic rows and
removes them at the end unless --keep). Reports, as JSON:

  * bulk load throughput (rows/s) for goal_search_index / procedure_search_index
  * HNSW + GIN index build time
  * lexical (FTS) latency p50/p95
  * vector ANN latency p50/p95
  * fused retrieval (both legs, RRF, visibility predicate) latency p50/p95 -- the real
    retrieval_service._legs code path
  * shard routing throughput (pure rendezvous hashing)
  * projection upsert throughput (search_projection.project_object on canonical goals)
  * ingestion worker throughput (bundles/s through the real lease -> handler -> complete loop
    with frozen fast model fakes; measures OUR overhead, not provider latency)

Synthetic vectors are random unit-ish vectors generated inside Postgres (no Python float
formatting), so the number to read is index/query cost at that cardinality, not model quality.
Model-quality numbers come from the golden suite and the optional live-model test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TAG = "zbench"
WORDS = ("deploy service database migrate schema callers function test flaky build cache index vector query "
         "python rust kubernetes docker secret rotate backup restore monitor alert latency throughput queue worker").split()


def log(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 2)


async def timed(coro_fn, n):
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        await coro_fn()
        lat.append((time.perf_counter() - t0) * 1000)
    return {"p50_ms": pct(lat, 0.50), "p95_ms": pct(lat, 0.95), "mean_ms": round(statistics.mean(lat), 2), "n": n}


async def main(a) -> dict:
    from app.db.session import create_pool
    from app.services.access import AccessScope
    from app.services.retrieval_service import RetrievalConfig, _legs
    from app.services.shards import ShardInfo, choose_shard

    pool = await create_pool(max_size=24)
    out: dict = {"goals": a.goals, "procedures": a.procedures}
    try:
        await pool.execute(f"DELETE FROM goal_search_index WHERE canonical_name LIKE '{TAG}%'")
        await pool.execute(f"DELETE FROM procedure_search_index WHERE name LIKE '{TAG}%'")
        async with pool.acquire() as c:
            await c.execute("SET maintenance_work_mem = '1GB'")
            log(f"loading {a.goals} synthetic goal projections")
            t0 = time.perf_counter()
            await c.execute("DROP INDEX IF EXISTS idx_goal_search_embedding")
            await c.execute(f"""
                INSERT INTO goal_search_index (goal_id, canonical_name, short_description, aliases, search_text, search_tsv,
                    embedding, embedding_model, embedding_version, embedding_dim, home_shard_id, status, version,
                    visibility, updated_at)
                SELECT gen_random_uuid(), '{TAG} ' || w.txt, NULL, '{{}}', '{TAG} ' || w.txt, to_tsvector('english', w.txt),
                       ('[' || (SELECT string_agg(((random() - 0.5) * 2)::text, ',') FROM generate_series(1, 1024) WHERE g.i > 0) || ']')::vector,
                       'bench-1', 'v1', 1024, 'K000', 'active', 1, 'public', now()
                FROM generate_series(1, {a.goals}) g(i)
                CROSS JOIN LATERAL (SELECT (SELECT string_agg((ARRAY[{','.join("'" + w + "'" for w in WORDS)}])[1 + floor(random() * {len(WORDS)})::int], ' ')
                                            FROM generate_series(1, 5) WHERE g.i > 0) AS txt) w
            """)
            load_s = time.perf_counter() - t0
            out["goal_bulk_load"] = {"seconds": round(load_s, 1), "rows_per_s": round(a.goals / load_s)}
            log("building HNSW index")
            t0 = time.perf_counter()
            await c.execute("CREATE INDEX idx_goal_search_embedding ON goal_search_index USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL")
            out["goal_hnsw_build_seconds"] = round(time.perf_counter() - t0, 1)
            await c.execute("ANALYZE goal_search_index")

        scope = AccessScope.unrestricted()
        cfg = RetrievalConfig()
        qv = await pool.fetchval("SELECT embedding::text FROM goal_search_index WHERE canonical_name LIKE $1 LIMIT 1", f"{TAG}%")
        qvec = [float(x) for x in qv.strip("[]").split(",")]

        async def lexical():
            q = " ".join(WORDS[i] for i in (uuid.uuid4().int % len(WORDS), uuid.uuid4().int % len(WORDS)))
            await pool.fetch("SELECT goal_id FROM goal_search_index WHERE status IN ('active','candidate') AND search_tsv @@ to_tsquery('english', $1) "
                             "ORDER BY ts_rank_cd(search_tsv, to_tsquery('english', $1)) DESC LIMIT 20", q.replace(" ", " | "))

        from app.services.embeddings import to_pgvector

        async def ann():
            await pool.fetch("SELECT goal_id FROM goal_search_index WHERE embedding IS NOT NULL AND embedding_model = 'bench-1' "
                             "ORDER BY embedding <=> $1::vector LIMIT 20", to_pgvector(qvec))

        async def fused():
            q = " ".join(WORDS[uuid.uuid4().int % len(WORDS)] for _ in range(3))
            await _legs(pool, table="goal_search_index", id_col="goal_id", name_col="canonical_name", text_expr="canonical_name",
                        extra_cols="", ctx_text=q, embedding=qvec, embedding_model="bench-1", scope=scope,
                        where_extra="status IN ('active', 'candidate')", extra_params=[], cfg=cfg)

        log("querying")
        out["lexical_fts"] = await timed(lexical, a.queries)
        out["vector_ann"] = await timed(ann, a.queries)
        out["fused_retrieval"] = await timed(fused, a.queries)
        # planner sanity: the two legs must use the indexes, not a seq scan
        plan = await pool.fetch("EXPLAIN SELECT goal_id FROM goal_search_index WHERE embedding IS NOT NULL AND embedding_model = 'bench-1' "
                                "ORDER BY embedding <=> $1::vector LIMIT 20", to_pgvector(qvec))
        out["ann_uses_hnsw"] = any("idx_goal_search_embedding" in r[0] for r in plan)

        log("shard routing")
        shards = [ShardInfo(f"K{i:03d}", "active", 100) for i in range(8)]
        t0 = time.perf_counter()
        for i in range(200_000):
            choose_shard(f"goal-{i}", shards)
        out["shard_routing"] = {"decisions_per_s": round(200_000 / (time.perf_counter() - t0))}

        if a.procedures:
            t0 = time.perf_counter()
            await pool.execute(f"""
                INSERT INTO procedure_search_index (procedure_id, procedure_row_id, name, summary, goal_id, search_text, search_tsv,
                    home_shard_id, status, version, visibility, updated_at)
                SELECT gen_random_uuid(), gen_random_uuid(), '{TAG} proc ' || i, 'bench', (SELECT goal_id FROM goal_search_index
                       WHERE canonical_name LIKE '{TAG}%' LIMIT 1), 'deploy database ' || i, to_tsvector('english', 'deploy database ' || i),
                       'K000', 'active', 1, 'public', now() FROM generate_series(1, {a.procedures}) i""")
            out["procedure_bulk_load"] = {"rows_per_s": round(a.procedures / (time.perf_counter() - t0))}

        log("projection upsert throughput")
        # projection upsert throughput on REAL canonical rows
        n_up = min(a.upserts, 2000)
        ids = []
        for i in range(n_up):
            ids.append(str(uuid.uuid4()))
        await pool.execute(
            f"INSERT INTO goals (id, canonical_name, normalized_name, status, scope_type, reconciled_at) "
            f"SELECT x::uuid, '{TAG}canon ' || x, '{TAG}canon ' || x, 'active', 'global', now() FROM unnest($1::text[]) x", ids)
        from app.services import search_projection as sp
        t0 = time.perf_counter()
        r = await sp.drain_outbox(pool, batch=500)
        dt = time.perf_counter() - t0
        out["projection_upsert"] = {"applied": r["applied"], "rows_per_s": round(r["applied"] / dt) if dt else None}
        await pool.execute(f"DELETE FROM goals WHERE canonical_name LIKE '{TAG}canon %'")
        await sp.drain_outbox(pool)

        log("FTS on a natural-shaped vocabulary")
        out["fts_zipf_natural_vocabulary"] = await fts_zipf_benchmark(pool, a.goals, a.queries)
        log("ingestion worker throughput")
        out["ingestion_worker"] = await worker_throughput(pool, a.bundles)
    finally:
        if not a.keep:
            await pool.execute(f"DELETE FROM goal_search_index WHERE canonical_name LIKE '{TAG}%'")
            await pool.execute(f"DELETE FROM procedure_search_index WHERE name LIKE '{TAG}%'")
        await pool.close()
    return out


async def fts_zipf_benchmark(pool, n_rows: int, queries: int) -> dict:
    """FTS latency on a NATURAL-shaped corpus: 20k-word vocabulary with a Zipf-like skew (a few very
    common words, a long tail), 8 words per goal, queries of 3 words drawn from the same skew (so they
    sometimes hit common words -- the realistic worst case for an OR query). Text only (no vectors)."""
    await pool.execute("DROP TABLE IF EXISTS zbench_fts")
    await pool.execute("CREATE UNLOGGED TABLE zbench_fts (id serial PRIMARY KEY, t text NOT NULL, tsv tsvector NOT NULL)")
    t0 = time.perf_counter()
    await pool.execute(f"""
        INSERT INTO zbench_fts (t, tsv)
        SELECT x.t, to_tsvector('simple', x.t) FROM (
            SELECT (SELECT string_agg('w' || floor(20000 * power(random(), 3))::int, ' ') FROM generate_series(1, 8) WHERE g.i > 0) AS t
            FROM generate_series(1, {n_rows}) g(i)) x""")
    load_s = time.perf_counter() - t0
    await pool.execute("CREATE INDEX ON zbench_fts USING gin (tsv)")
    await pool.execute("ANALYZE zbench_fts")

    async def q3():
        words = [f"w{int(20000 * (uuid.uuid4().int % 10**6 / 10**6) ** 3)}" for _ in range(3)]
        tq = " | ".join(words)
        await pool.fetch("SELECT id FROM zbench_fts WHERE tsv @@ to_tsquery('simple', $1) "
                         "ORDER BY ts_rank_cd(tsv, to_tsquery('simple', $1)) DESC LIMIT 20", tq)

    res = await timed(q3, queries)
    res.update({"rows": n_rows, "load_seconds": round(load_s, 1), "vocabulary": 20000})
    await pool.execute("DROP TABLE zbench_fts")
    return res


async def worker_throughput(pool, n_bundles: int) -> dict:
    """Real lease -> handler -> complete loop; fast frozen model fakes => measures our overhead."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
    from dataclasses import replace

    from app.ingestion import queue as q
    from app.ingestion.config import WorkerConfig
    from app.ingestion.handlers import JOB_TYPE, Dependencies
    from app.ingestion.worker import Worker
    from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge

    Dependencies.configure(embedder=ConceptEmbedder(), judge=make_judge(FrozenProvider({})))
    tag = f"{TAG}w{uuid.uuid4().hex[:6]}"
    for i in range(n_bundles):
        await q.enqueue(pool, JOB_TYPE, {"source_key": f"{tag}-{i}", "goal": f"{TAG} goal {tag} {i}", "provenance": "prior_library",
                                        "procedure": {"name": f"{TAG} proc {tag} {i}", "steps": [{"description": "x"}]}, "claims": []},
                        idempotency_key=f"{tag}-{i}", scope_type="global")
    cfg = WorkerConfig(concurrency=4, lease_seconds=60, poll_seconds=0.05, reconcile_goals=False, drain_projections=True)
    t0 = time.perf_counter()
    res = await asyncio.gather(*[Worker(pool, replace(cfg), worker_id=f"bench{i}").run(loop=False) for i in range(2)])
    dt = time.perf_counter() - t0
    done = sum(r["done"] for r in res)
    await pool.execute("DELETE FROM ingestion_jobs WHERE idempotency_key LIKE $1", f"{tag}-%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{TAG} proc {tag}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{TAG}%")
    await pool.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{TAG} goal {tag}%")
    await pool.execute("DELETE FROM ingestion_contexts WHERE source_hash LIKE $1", f"{tag}-%")
    Dependencies.configure()
    return {"bundles": n_bundles, "done": done, "seconds": round(dt, 2), "bundles_per_s": round(done / dt, 1) if dt else None,
            "workers": 2, "lanes_each": 4}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--goals", type=int, default=100_000)
    ap.add_argument("--procedures", type=int, default=20_000)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--upserts", type=int, default=1000)
    ap.add_argument("--bundles", type=int, default=200)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--only-fts-zipf", action="store_true", help="just the natural-vocabulary FTS latency benchmark")
    args = ap.parse_args()

    async def _only_fts():
        from app.db.session import create_pool
        pool = await create_pool(max_size=2)
        try:
            return {"fts_zipf_natural_vocabulary": await fts_zipf_benchmark(pool, args.goals, args.queries)}
        finally:
            await pool.close()

    print(json.dumps(asyncio.run(_only_fts() if args.only_fts_zipf else main(args)), indent=2))
