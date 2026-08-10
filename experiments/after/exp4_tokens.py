"""
Experiment 4 -- SLM trajectory transfer, token cost. Hosted 2x2.

                    | No skill            | TaskNode trajectory
  SLM  (8B, Groq)   | baseline tokens     | test tokens
  Large (120B, GC)  | reference ceiling   | control -- should barely move

Both arms are HOSTED and pinned to a published snapshot. Local Ollama was
used in a first pass and is deliberately not used here: `llama3.1:8b` is
whatever that tag pointed at on the day it was pulled, so a number measured
against it cannot be reproduced by anyone else.

ACCURACY IS NOT REPORTED, and that is a property of the substrate rather
than a shortcut. AFTER ships zero validators (no test*.py anywhere in the
dataset), so there is no objective pass/fail for generated output. An
LLM-judge stand-in would not support the plan's "closes the gap to
frontier" claim, so the claim is not made. Token cost is measured instead,
which is exact, provider-reported, and needs no grader.

The open question this can answer: supplying a retrieved procedure costs
prompt tokens up front. Does it buy back more than it costs in shorter
completions, and does that trade differ between an 8B and a 120B model?
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

from openai import AsyncOpenAI
from scipy.stats import wilcoxon

from app.config import settings
from app.db.session import create_pool
from app.services.embeddings import to_pgvector
from experiments.after.corpus import load_tasks
from experiments.after.embed_cache import CachedEmbedder

LIBRARY_CREATED_BY = "after_experiment"

# (label, provider, model). Chosen from what check_slm.py actually
# enumerated, not from memory of either catalog.
ARMS = [
    ("slm_8b", "groq", "llama-3.1-8b-instant"),
    ("large_120b", "general_compute", "gpt-oss-120b"),
]

SYSTEM = (
    "You are a senior engineer. Produce a concise, concrete plan of the steps "
    "required to complete the task. Be specific about files, formats, and "
    "operations. Do not write the full implementation."
)


def client_for(provider: str) -> AsyncOpenAI:
    if provider == "groq":
        return AsyncOpenAI(api_key=settings.require("groq_api_key"),
                           base_url=settings.groq_base_url)
    if provider == "general_compute":
        return AsyncOpenAI(api_key=settings.require("general_compute_api_key"),
                           base_url=settings.general_compute_base_url)
    if provider == "cerebras":
        return AsyncOpenAI(api_key=settings.require("cerebras_api_key"),
                           base_url=settings.cerebras_base_url)
    raise ValueError(f"unknown provider {provider!r}")


async def retrieved_context(pool, embedder, instruction: str, k: int = 2) -> str:
    """
    The graph's contribution: the top-k retrieved skill nodes, rendered as
    the procedure to follow. Excludes hierarchy group nodes, whose names
    are generated cluster labels rather than content.
    """
    qvec = await embedder.embed_one(instruction, input_type="query")
    rows = await pool.fetch(
        "SELECT name, description, 1 - (embedding <=> $1::vector) AS sim "
        "FROM task_nodes WHERE created_by = $2 AND t_invalid IS NULL "
        "AND embedding IS NOT NULL ORDER BY sim DESC LIMIT $3",
        to_pgvector(qvec), LIBRARY_CREATED_BY, k,
    )
    if not rows:
        return ""
    parts = ["RELEVANT PROCEDURE FROM THE KNOWLEDGE GRAPH:"]
    for r in rows:
        parts.append(f"\n## {r['name']} (similarity {float(r['sim']):.2f})\n"
                     f"{(r['description'] or '')[:2500]}")
    return "\n".join(parts)


async def one_call(client, model: str, user: str, retries: int = 4) -> dict | None:
    for attempt in range(retries):
        try:
            t0 = time.time()
            r = await asyncio.wait_for(client.chat.completions.create(
                model=model, temperature=0, max_tokens=900,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": user}],
            ), timeout=300.0)
            return {
                "prompt_tokens": r.usage.prompt_tokens,
                "completion_tokens": r.usage.completion_tokens,
                "total_tokens": r.usage.total_tokens,
                "seconds": round(time.time() - t0, 1),
            }
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if attempt == retries - 1 or not any(
                s in msg for s in ("rate", "429", "blocked", "timeout", "503", "502")
            ):
                print(f"      FAIL {model}: {type(exc).__name__}: {str(exc)[:140]}")
                return None
            await asyncio.sleep(15 * (2 ** attempt))
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-interval", type=float, default=21.0)
    args = ap.parse_args()

    tasks = [t for t in load_tasks() if t.is_composite][: args.limit]
    print(f"{len(tasks)} composite tasks x {len(ARMS)} models x 2 conditions "
          f"= {len(tasks)*len(ARMS)*2} generations")
    for label, provider, model in ARMS:
        print(f"  {label:12s} {provider}/{model}")

    pool = await create_pool(min_size=1, max_size=4)
    embedder = CachedEmbedder(min_interval=args.min_interval)
    rows = []
    try:
        contexts = {}
        for t in tasks:
            contexts[t.task_id] = await retrieved_context(pool, embedder, t.instruction)

        for label, provider, model in ARMS:
            client = client_for(provider)
            for i, t in enumerate(tasks, 1):
                ctx = contexts[t.task_id]
                bare = await one_call(client, model, t.instruction)
                withctx = await one_call(client, model, f"{ctx}\n\nTASK:\n{t.instruction}")
                if not bare or not withctx:
                    continue
                rows.append({
                    "arm": label, "provider": provider, "model": model,
                    "task_id": t.task_id, "gold": t.gold_skills,
                    "no_context": bare, "with_context": withctx,
                    "context_chars": len(ctx),
                })
                print(f"  [{i}/{len(tasks)}] {label} {t.task_id}: "
                      f"bare p{bare['prompt_tokens']}/c{bare['completion_tokens']} -> "
                      f"ctx p{withctx['prompt_tokens']}/c{withctx['completion_tokens']}")

        summary = {}
        for label, provider, model in ARMS:
            m = [r for r in rows if r["arm"] == label]
            if not m:
                summary[label] = {"n": 0, "note": "no successful generations"}
                continue

            def mean(cond: str, field: str) -> float:
                return round(statistics.mean(r[cond][field] for r in m), 1)

            cb = [r["no_context"]["completion_tokens"] for r in m]
            cc = [r["with_context"]["completion_tokens"] for r in m]
            diffs = [a - b for a, b in zip(cb, cc)]  # positive = context shortened output
            try:
                p = round(float(wilcoxon(diffs).pvalue), 6) if any(d for d in diffs) else 1.0
            except Exception:  # noqa: BLE001
                p = None
            summary[label] = {
                "provider": provider, "model": model, "n": len(m),
                "no_context": {"prompt": mean("no_context", "prompt_tokens"),
                               "completion": mean("no_context", "completion_tokens"),
                               "total": mean("no_context", "total_tokens")},
                "with_context": {"prompt": mean("with_context", "prompt_tokens"),
                                 "completion": mean("with_context", "completion_tokens"),
                                 "total": mean("with_context", "total_tokens")},
                "completion_delta_mean": round(statistics.mean(diffs), 1),
                "completion_wilcoxon_p": p,
                "total_token_ratio_ctx_over_bare": round(
                    mean("with_context", "total_tokens") / mean("no_context", "total_tokens"), 3),
            }

        out = {"summary": summary, "rows": rows}
        print("\n" + json.dumps(summary, indent=2))
        Path(__file__).parent.joinpath("results_exp4_tokens.json").write_text(
            json.dumps(out, indent=2), encoding="utf-8")
        print("\nwrote results_exp4_tokens.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
