"""
Experiment 1, Hypothesis B -- subtask-level retrieval (Part C).

For a COMPOSITE task (2-3 gold skills), does per-subtask resolution
identify each component individually, where whole-task retrieval recovers
at most one?

  B-flat     whole instruction -> flat cosine -> top-|gold| skill names
  B-subtask  decompose the instruction -> resolve_subtask_reuse over the
             proposed ops -> the skills its candidates point at

THRESHOLD IS SWEPT OFFLINE, NOT FIXED IN ADVANCE. resolve_subtask_reuse
now reports every op's best candidate and score regardless of threshold
(SubtaskReuseReport.candidates), so one expensive decomposition pass
yields recall at every threshold. This matters because the library's own
pairwise cosine tops out at 0.7576 -- the FULL_MATCH_THRESHOLD of 0.90
that resolve_subtask_reuse defaults to sits above the entire observed
distribution, so a single-threshold run would report zero matches and be
misread as "the mechanism does not work".

CONFOUND, measured rather than hidden: DecompositionService already runs
find_reusable_nodes on the whole problem first, and injects any partial
match into the generator prompt as "these already exist and must NOT be
recreated". Components caught there never become proposed ops, so Part C
cannot match them. Reporting Part C's yield alone would therefore
understate the pipeline and overstate the confound. Both are recorded:
`pipeline_recall` is what the system as a whole identifies, `partc_recall`
is Part C's marginal contribution on top of that.
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

from scipy.stats import binomtest, wilcoxon

from app.config import settings
from app.db.session import create_pool
from app.debate.panel import OpenAICompatAgent, default_judge, default_panel
from app.services.access import AccessScope
from app.services.decomposition import DecompositionService
from app.services.embeddings import to_pgvector
from app.services.retrieval import HybridRetriever
from experiments.after.corpus import load_tasks
from experiments.after.embed_cache import CachedEmbedder

LIBRARY_CREATED_BY = "after_experiment"
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(14)]  # 0.30 .. 0.95


async def flat_topk(pool, query_vec, k: int) -> list[str]:
    rows = await pool.fetch(
        "SELECT name, 1 - (embedding <=> $1::vector) AS sim FROM task_nodes "
        "WHERE created_by = $2 AND t_invalid IS NULL AND embedding IS NOT NULL "
        "ORDER BY sim DESC LIMIT $3",
        to_pgvector(query_vec), LIBRARY_CREATED_BY, k,
    )
    return [r["name"] for r in rows]


def _provider_blocked(decomp) -> bool:
    """
    The provider returns an HTML block page under load, and
    DecompositionService catches the resulting JSON parse failure and
    reports feasible=False -- indistinguishable, in the result object,
    from the model judging the task genuinely infeasible. A first run at
    concurrency 3 recorded 30/40 tasks as "infeasible" this way; every one
    was a WAF block. Detected and retried rather than scored.
    """
    if decomp.feasible:
        return False
    r = (decomp.reasoning or "").lower()
    return any(s in r for s in (
        "<!doctype", "<html", "blocked", "429", "too many requests",
        # Groq reports a per-minute token ceiling as 413 rate_limit_exceeded,
        # which is transient and retryable, not a verdict on the task.
        "rate_limit", "rate limit", "413", "request too large", "tokens per minute",
        "502", "503", "timeout",
    ))


async def run_one(task, pool, embedder, service, scope, sem) -> dict:
    gold = set(task.gold_skills)
    async with sem:
        t0 = time.time()
        decomp = None
        for attempt in range(5):
            try:
                decomp = await service.decompose(task.instruction)
            except Exception as exc:  # noqa: BLE001
                return {"task_id": task.task_id, "error": f"{type(exc).__name__}: {exc}"[:300]}
            if not _provider_blocked(decomp):
                break
            backoff = 20 * (2 ** attempt)
            print(f"    {task.task_id}: provider blocked, retrying in {backoff}s")
            await asyncio.sleep(backoff)
        else:
            return {"task_id": task.task_id, "error": "provider blocked after 5 attempts"}
        elapsed = time.time() - t0

    # What the top-level reuse check already found, before Part C ran.
    prior = {r["name"] for r in decomp.reused_nodes}

    # DecompositionService already runs resolve_subtask_reuse internally
    # (decomposition.py:402) and returns the shrunk ChangeSet. Calling it
    # again here would re-scan a proposal whose matched ops have already
    # been removed and find nothing. The candidates it recorded are read
    # off the result instead.
    ops = len(decomp.change_set.ops)
    candidates = decomp.subtask_candidates

    qvec = await embedder.embed_one(task.instruction, input_type="query")
    flat = await flat_topk(pool, qvec, max(1, len(gold)))
    flat1 = await flat_topk(pool, qvec, 1)

    per_threshold = {}
    for t in THRESHOLDS:
        matched = {c["matched_name"] for c in candidates
                   if c["similarity"] is not None and c["similarity"] >= t}
        partc_hits = matched & gold
        pipeline_hits = (matched | prior) & gold
        per_threshold[str(t)] = {
            "partc_recall": len(partc_hits) / len(gold),
            "pipeline_recall": len(pipeline_hits) / len(gold),
            "false_components": len(matched - gold),
            "matched": sorted(matched),
        }

    return {
        "task_id": task.task_id,
        "role": task.role,
        "gold": sorted(gold),
        "n_gold": len(gold),
        "n_ops": ops,
        "feasible": decomp.feasible,
        "prior_reuse": sorted(prior),
        "prior_recall": len(prior & gold) / len(gold),
        "flat_topk": flat,
        "flat_recall": len(set(flat) & gold) / len(gold),
        "flat_top1_recall": len(set(flat1) & gold) / len(gold),
        "candidate_similarities": sorted(
            (round(c["similarity"], 4) for c in candidates if c["similarity"] is not None),
            reverse=True,
        ),
        "candidate_methods": sorted({c["method"] for c in candidates}),
        "per_threshold": per_threshold,
        "seconds": round(elapsed, 1),
    }


def aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    if not ok:
        return {"error": "no successful rows"}

    flat_recall = [r["flat_recall"] for r in ok]
    sims = [s for r in ok for s in r["candidate_similarities"]]

    by_threshold = {}
    for t in THRESHOLDS:
        key = str(t)
        partc = [r["per_threshold"][key]["partc_recall"] for r in ok]
        pipe = [r["per_threshold"][key]["pipeline_recall"] for r in ok]
        false_c = [r["per_threshold"][key]["false_components"] for r in ok]

        # Paired tests, pipeline vs flat, on the SAME tasks.
        pipe_all = [p >= 1.0 for p in pipe]
        flat_all = [f >= 1.0 for f in flat_recall]
        b = sum(1 for x, y in zip(pipe_all, flat_all) if x and not y)
        c = sum(1 for x, y in zip(pipe_all, flat_all) if y and not x)
        mc = (round(float(binomtest(b, b + c, 0.5).pvalue), 6) if (b + c) else 1.0)
        diffs = [p - f for p, f in zip(pipe, flat_recall)]
        try:
            w = round(float(wilcoxon(diffs).pvalue), 6) if any(d != 0 for d in diffs) else 1.0
        except Exception:  # noqa: BLE001
            w = None

        by_threshold[key] = {
            "partc_recall_mean": round(statistics.mean(partc), 4),
            "pipeline_recall_mean": round(statistics.mean(pipe), 4),
            "false_components_mean": round(statistics.mean(false_c), 3),
            "pipeline_recovered_all": sum(pipe_all),
            "flat_recovered_all": sum(flat_all),
            "mcnemar_p": mc,
            "wilcoxon_p": w,
        }

    return {
        "n_tasks": len(ok),
        "n_errors": len(rows) - len(ok),
        "flat_recall_mean": round(statistics.mean(flat_recall), 4),
        "flat_top1_recall_mean": round(statistics.mean(r["flat_top1_recall"] for r in ok), 4),
        "prior_recall_mean": round(statistics.mean(r["prior_recall"] for r in ok), 4),
        "mean_ops_per_task": round(statistics.mean(r["n_ops"] for r in ok), 2),
        "candidate_similarity": {
            "n": len(sims),
            "min": round(min(sims), 4) if sims else None,
            "max": round(max(sims), 4) if sims else None,
            "mean": round(statistics.mean(sims), 4) if sims else None,
            "median": round(statistics.median(sims), 4) if sims else None,
        },
        "by_threshold": by_threshold,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--min-interval", type=float, default=21.0)
    ap.add_argument("--out", default="results_exp1_hyp_b.json")
    # General Compute serves HTML block pages under sustained load; a first
    # attempt spent 83 minutes in backoff without finishing 40 tasks. Groq
    # answers the same workload without blocking, so it is the default here.
    # Generator and critic stay in different model families either way --
    # a model reviewing its own output shares the blind spot that produced
    # the flaw.
    ap.add_argument("--provider", default="groq", choices=["groq", "general_compute"])
    args = ap.parse_args()

    tasks = [t for t in load_tasks() if t.is_composite]
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"{len(tasks)} composite tasks, {sum(len(t.gold_skills) for t in tasks)} gold components")

    pool = await create_pool(min_size=2, max_size=8)
    embedder = CachedEmbedder(min_interval=args.min_interval)
    scope = AccessScope.unrestricted()
    if args.provider == "groq":
        def groq_agent(agent_id: str, model_id: str, family: str) -> OpenAICompatAgent:
            return OpenAICompatAgent(
                agent_id=agent_id, model_id=model_id, family=family,
                api_key_field="groq_api_key", base_url=settings.groq_base_url,
                # 2000 (the default) truncates a multi-subtask ChangeSet
                # mid-JSON, which surfaces as an unparseable response and
                # is then indistinguishable from a refusal.
                max_tokens=6000,
            )
        generator = groq_agent("groq-generator", "llama-3.3-70b-versatile", "llama")
        critic = groq_agent("groq-critic", "openai/gpt-oss-120b", "gpt-oss")
    else:
        panel = default_panel()
        generator = panel[0]
        critic = panel[1] if len(panel) > 1 else default_judge()
    print(f"provider={args.provider} generator={generator.model_id} critic={critic.model_id}")

    service = DecompositionService(
        generator=generator,
        critic=critic,
        retriever=HybridRetriever(pool, scope=scope, embedder=embedder),
    )
    sem = asyncio.Semaphore(args.concurrency)

    try:
        t0 = time.time()
        rows = []
        coros = [run_one(t, pool, embedder, service, scope, sem) for t in tasks]
        for i, fut in enumerate(asyncio.as_completed(coros), 1):
            row = await fut
            rows.append(row)
            tag = "ERR" if "error" in row else f"ops={row['n_ops']}"
            print(f"  [{i}/{len(tasks)}] {row['task_id']}: {tag}")

        summary = aggregate(rows)
        out = {"summary": summary, "rows": rows,
               "wall_seconds": round(time.time() - t0, 1),
               "embedder": embedder.stats()}
        print("\n" + json.dumps(summary, indent=2))
        Path(__file__).parent.joinpath(args.out).write_text(
            json.dumps(out, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
