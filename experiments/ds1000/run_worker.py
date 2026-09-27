"""Run the REAL ingestion Worker (app.ingestion.worker.Worker) in-process against the isolated
experiment database, for the job types Kel's own writes enqueued (e.g. goal_abstraction_placement).
No service credential: the experiment DB is local and isolated (the production CLI refuses to start
without one outside TEST; the Worker class itself is unchanged).

    python run_worker.py goal_abstraction_placement[,other_type]
"""
from __future__ import annotations

import asyncio
import sys

import demo_env


async def main(job_types: list[str]) -> None:
    demo_env.verify_after_import()
    from app.db.session import create_pool
    from app.ingestion.config import WorkerConfig
    from app.ingestion.worker import Worker
    from app.services.shards import ShardPools

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=12)
    try:
        worker = Worker(pool, WorkerConfig(concurrency=4), worker_id="ds1000-experiment", pools=ShardPools(pool),
                        service=None, job_types=job_types)
        print(await worker.run(loop=False))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].split(",")))
