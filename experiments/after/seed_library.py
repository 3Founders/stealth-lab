"""
Build the experiment library: 22 AFTER skills -> task_nodes -> hierarchy.

Idempotent by design: re-running wipes only what this script created
(provenance='company_ingested' rows tagged by created_by) and rebuilds.
An experiment corpus that silently doubles on a re-run would quietly
change every retrieval number downstream.

Leakage note: the library is built ONLY from skills/*/SKILL.md. No task
instruction text enters it. Skills and tasks are independent artifacts in
AFTER, and the gold label lives in task.toml -- so there is no path by
which a query's own text can be retrieved as its own answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg

from app.config import settings
from app.db.session import create_pool
from app.onboarding.seed import Onboarder, TaskSpec, WorkflowSpec
from app.services.access import AccessScope
from app.services.hierarchy import build_hierarchy_for_table
from experiments.after.corpus import load_skills, summary
from experiments.after.embed_cache import CachedEmbedder

CREATED_BY = "after_experiment"
WORKFLOW = "AFTER skill library"


async def wipe(pool: asyncpg.Pool) -> None:
    """Remove everything a previous run of this script created."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM edges WHERE created_by = $1 OR source_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1) OR target_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1)",
                CREATED_BY,
            )
            # Hierarchy internal nodes are written by build_hierarchy_for_table
            # with its own created_by, so clear those too or the next build
            # stacks a second tree on top of the first.
            await conn.execute("DELETE FROM edges WHERE created_by = 'hierarchy_builder'")
            await conn.execute("DELETE FROM task_nodes WHERE created_by IN ($1, 'hierarchy_builder')", CREATED_BY)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="SKILL.md", choices=["SKILL.md", "SKILL_HANDCRAFT.md"])
    ap.add_argument("--min-interval", type=float, default=21.0,
                    help="seconds between embedding requests (Voyage free tier = 3/min)")
    ap.add_argument("--skip-hierarchy", action="store_true")
    args = ap.parse_args()

    info = summary()
    print(json.dumps(info, indent=2))
    if info["unknown_gold_labels"]:
        print("FAIL: gold labels reference skills absent from the library")
        return 1

    skills = load_skills(variant=args.variant)
    # create_pool, not asyncpg.create_pool: it registers the JSONB codec,
    # without which every dict-valued column (io_schema, properties,
    # success_criteria) fails to bind. See app/db/session.py.
    pool = await create_pool(min_size=1, max_size=4)
    embedder = CachedEmbedder(min_interval=args.min_interval)

    try:
        await wipe(pool)

        spec = WorkflowSpec(
            workflow_name=WORKFLOW,
            tasks=[
                TaskSpec(key=s.name, name=s.name, description=s.body)
                for s in skills
            ],
            edges=[],
        )
        # embed=False: Onboarder would use the unthrottled production
        # Embedder and get rate-limited partway through, leaving a library
        # with some rows embedded and some NULL -- which every retrieval
        # path then silently downgrades to lexical without erroring.
        t0 = time.time()
        result = await Onboarder(pool).seed(spec, created_by=CREATED_BY, embed=False)
        print(f"seeded {len(result.task_ids)} skill nodes in {time.time()-t0:.1f}s")

        print(f"embedding {len(skills)} skill documents (throttled, cached)...")
        t0 = time.time()
        texts = [s.text() for s in skills]
        vectors = await embedder.embed(texts, input_type="document")
        from app.services.embeddings import to_pgvector
        async with pool.acquire() as conn:
            for s, vec in zip(skills, vectors):
                await conn.execute(
                    "UPDATE task_nodes SET embedding = $2::vector WHERE id = $1",
                    result.task_ids[s.name], to_pgvector(vec),
                )
        print(f"embedded in {time.time()-t0:.1f}s  stats={embedder.stats()}")

        missing = await pool.fetchval(
            "SELECT count(*) FROM task_nodes WHERE created_by = $1 AND embedding IS NULL",
            CREATED_BY,
        )
        if missing:
            print(f"FAIL: {missing} library nodes have no embedding; retrieval would "
                  f"silently fall back to lexical and measure the wrong mechanism")
            return 1

        if not args.skip_hierarchy:
            print("building hierarchy over task_nodes...")
            t0 = time.time()
            report = await build_hierarchy_for_table(
                pool, "task_nodes", scope=AccessScope.unrestricted(),
                embedder=embedder, apply=True,
            )
            print(f"hierarchy built in {time.time()-t0:.1f}s: {report}")

        counts = await pool.fetchrow(
            "SELECT (SELECT count(*) FROM task_nodes WHERE t_invalid IS NULL) AS nodes, "
            "(SELECT count(*) FROM edges WHERE t_invalid IS NULL AND edge_type='OWNS' "
            "  AND custom_edge_type='PARENT_OF') AS parent_edges"
        )
        print(f"\nfinal: {counts['nodes']} live task_nodes, {counts['parent_edges']} PARENT_OF edges")
        manifest = {
            "variant": args.variant,
            "skills": len(skills),
            "live_task_nodes": counts["nodes"],
            "parent_of_edges": counts["parent_edges"],
            "embedding_model": embedder.model,
        }
        Path(__file__).parent.joinpath("library_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print("wrote library_manifest.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
