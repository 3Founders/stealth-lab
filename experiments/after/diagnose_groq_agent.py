"""Why did the Groq-backed decomposition return feasible=False instantly?"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.db.session import create_pool
from app.debate.panel import OpenAICompatAgent
from app.services.access import AccessScope
from app.services.decomposition import DecompositionService
from app.services.retrieval import HybridRetriever
from experiments.after.corpus import load_tasks
from experiments.after.embed_cache import CachedEmbedder


def agent(aid, model, family, max_tokens=6000):
    return OpenAICompatAgent(agent_id=aid, model_id=model, family=family,
                             api_key_field="groq_api_key",
                             base_url=settings.groq_base_url, max_tokens=max_tokens)


async def main() -> int:
    gen = agent("g", "llama-3.3-70b-versatile", "llama")

    print("=== 1. raw agent.respond ===")
    try:
        out = await gen.respond("You are terse.", "Reply with the JSON {\"ok\": true} only.")
        print("OK:", repr(out[:300]))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}: {str(exc)[:600]}")

    print("\n=== 2. same with a smaller max_tokens ===")
    try:
        out = await agent("g2", "llama-3.3-70b-versatile", "llama", 2000).respond(
            "You are terse.", "Reply with the JSON {\"ok\": true} only.")
        print("OK:", repr(out[:300]))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}: {str(exc)[:600]}")

    print("\n=== 3. full decompose, reasoning surfaced ===")
    pool = await create_pool(min_size=1, max_size=2)
    try:
        task = [t for t in load_tasks() if t.is_composite][0]
        svc = DecompositionService(
            generator=gen,
            critic=agent("c", "openai/gpt-oss-120b", "gpt-oss"),
            retriever=HybridRetriever(pool, scope=AccessScope.unrestricted(),
                                      embedder=CachedEmbedder()),
        )
        d = await svc.decompose(task.instruction)
        print("feasible:", d.feasible)
        print("ops:", len(d.change_set.ops))
        print("reasoning:", (d.reasoning or "")[:800])
        print("structural_problems:", d.structural_problems)
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
