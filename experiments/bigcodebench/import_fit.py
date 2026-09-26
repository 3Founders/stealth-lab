"""Import ONLY the sample's fit tasks into the demo database (held-out tasks never enter
Kel: they arrive later as find_ways queries, like a real user's task).

    python import_fit.py [--no-embed]
"""
from __future__ import annotations

import argparse
import asyncio
import json

import demo_env

from app.benchmarks import bigcodebench as bcb
from app.benchmarks.importer import import_tasks
from app.benchmarks.tasks import assign_splits


async def main(embed: bool) -> None:
    demo_env.verify_after_import()
    from app.db.session import create_pool

    sample = json.loads((demo_env.RUNS / "sample.json").read_text(encoding="utf-8"))
    tasks = bcb.tasks_from_rows(bcb.load_rows(demo_env.DATA / f"bigcodebench-{bcb.LATEST_VERSION}.parquet"))
    assign_splits(tasks)
    fit = [t for t in tasks if t.external_id in set(sample["fit"])]
    assert all(t.split == "fit" for t in fit) and len(fit) == len(sample["fit"])
    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        embedder = None
        if embed:
            from app.services.embeddings import Embedder
            embedder = Embedder(rate_limit_pool=pool)
        report = await import_tasks(pool, fit, embedder=embedder)
    finally:
        await pool.close()
    manifest = report.pop("manifest")
    (demo_env.RUNS / "manifest_fit.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    report.pop("summary")
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    asyncio.run(main(not ap.parse_args().no_embed))
