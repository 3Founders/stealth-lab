"""Why did 30/40 composite tasks decompose into zero ops?"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import create_pool
from app.debate.panel import default_judge, default_panel
from app.services.access import AccessScope
from app.services.decomposition import DecompositionService
from app.services.retrieval import HybridRetriever
from experiments.after.corpus import load_tasks
from experiments.after.embed_cache import CachedEmbedder


async def main() -> int:
    tasks = [t for t in load_tasks() if t.is_composite][:4]
    pool = await create_pool(min_size=1, max_size=4)
    embedder = CachedEmbedder()
    scope = AccessScope.unrestricted()
    panel = default_panel()
    service = DecompositionService(
        generator=panel[0],
        critic=panel[1] if len(panel) > 1 else default_judge(),
        retriever=HybridRetriever(pool, scope=scope, embedder=embedder),
    )
    try:
        for t in tasks:
            print("=" * 70)
            print(f"{t.task_id}  gold={t.gold_skills}  instruction_chars={len(t.instruction)}")
            d = await service.decompose(t.instruction)
            print(f"  feasible            : {d.feasible}")
            print(f"  ops                 : {len(d.change_set.ops)}")
            print(f"  reasoning           : {d.reasoning[:400]}")
            print(f"  structural_problems : {d.structural_problems}")
            print(f"  objections          : {d.objections[:3]}")
            print(f"  input_flags         : {d.input_flags}")
            print(f"  input_truncated     : {d.input_truncated}")
            print(f"  reused_nodes        : {[r['name'] for r in d.reused_nodes]}")
            print(f"  subtask_candidates  : {len(d.subtask_candidates)}")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
