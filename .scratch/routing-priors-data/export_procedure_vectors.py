"""One-time, read-only export (rule: keep Neon egress minimal -- export once to local files).

For every benchmark task we ingested (ingest_task_goals, swe:*), the procedures that achieve its Goal: id, goal id,
embedding (float4, binary protocol: ~4 KB a row). Written to procedure_vectors.npz next to this file. Prints counts
only; never a DSN.
"""
import asyncio
import os
import time

import asyncpg
import numpy as np

from app.db.session import create_pool
from app.services import shards as S

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "vertex:gemini-embedding-2"


async def main() -> None:
    pool = await create_pool(min_size=1, max_size=1)
    try:
        rows = await pool.fetch("SELECT task_key, goal_id::text AS g FROM ingest_task_goals WHERE task_key LIKE 'swe:%'")
        task_goal = {r["task_key"][4:]: r["g"] for r in rows}
        goals = sorted(set(task_goal.values()))
        shards = [s for s in S.knowledge_members(await S.list_shards(pool)) if s.dsn_env]
    finally:
        await pool.close()
    print(f"{len(task_goal)} tasks -> {len(goals)} goals; {len(shards)} shards", flush=True)
    ids, gids, vecs = [], [], []
    t0 = time.time()
    for i, s in enumerate(shards):
        dsn = S.shard_dsn(s.dsn_env)
        if not dsn:
            continue
        c = await asyncpg.connect(dsn, timeout=60)
        try:
            got = await c.fetch(
                "SELECT id::text AS id, achieves_goal_id::text AS g, embedding::float4[] AS v FROM procedures "
                "WHERE achieves_goal_id = ANY($1::uuid[]) AND embedding IS NOT NULL AND embedding_model_id = $2",
                goals, MODEL)
        finally:
            await c.close()
        for r in got:
            ids.append(r["id"])
            gids.append(r["g"])
            vecs.append(np.asarray(r["v"], dtype=np.float32))
        if i % 10 == 0:
            print(f"  shard {i + 1}/{len(shards)}: {len(ids)} procedures ({time.time() - t0:.0f}s)", flush=True)
    np.savez_compressed(os.path.join(HERE, "procedure_vectors.npz"), ids=np.array(ids), goal_ids=np.array(gids),
                        vectors=np.stack(vecs) if vecs else np.zeros((0, 1024), np.float32),
                        task_ids=np.array(list(task_goal)), task_goals=np.array(list(task_goal.values())),
                        model=np.array(MODEL))
    print(f"done: {len(ids)} procedures over {len(set(gids))} goals ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main())
