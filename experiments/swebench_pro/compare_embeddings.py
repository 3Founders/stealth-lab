"""
Split vs joint representation, measured on the same corpus and queries.

  embedding        title + problem statement          (issue only)
  embedding_joint  title + problem statement + DIFF   (issue + resolution)

THE HYPOTHESIS THIS TESTS

Issue-only vectors cannot distinguish two senses of a domain word. On the
flipt instance, "Flipt Fails to Authenticate with AWS ECR Registries"
(registry login, internal/oci/ecr/*) outranked "Authentication cookies are
not cleared" (request middleware) for a query about auth middleware -- both
problem statements use the same vocabulary and only the DIFFS separate them.
Folding the diff into the document vector should fix exactly that.

THE OBJECTION, STATED PLAINLY

The query side stays issue-only, so the comparison becomes asymmetric:
an issue-shaped query against issue+diff documents. That asymmetry could
hurt as easily as help, which is why this measures rather than assumes.

WHY THIS IS READ-ONLY AND VECTOR-ONLY

Read-only: run_graph_experiment.py owns the mutable graph state -- it sets
t_invalid per instance and rebuilds the hierarchy -- so this must not use
hold_out(). Self-matches are excluded in the SQL by instance id instead,
which gives the same leave-one-out semantics without mutating a row.

Vector-only: HybridRetriever fuses vector with a lexical leg that is
IDENTICAL under both columns, so an RRF comparison would dilute the very
difference being measured. Comparing the raw distance ordering isolates the
representation change.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE.parents[1]))

from app.db.session import create_pool  # noqa: E402
from app.services.embeddings import to_pgvector  # noqa: E402
from app.services.embed_cache import CachedEmbedder  # noqa: E402
from graph_ingest import (  # noqa: E402
    SWEBENCH_DSN, load_dataset, normalize_statement, patch_facts, title_of,
)

SQL = """
SELECT t.skill_ref AS iid, k.properties AS props,
       1 - (t.{col} <=> $1::vector) AS sim
FROM task_nodes t
JOIN knowledge_nodes k ON k.properties->>'instance_id' = t.skill_ref
WHERE t.created_by = 'swebench_ingest'
  AND t.{col} IS NOT NULL
  AND t.skill_ref <> $2          -- leave-one-out, without mutating t_invalid
ORDER BY t.{col} <=> $1::vector ASC
LIMIT $3
"""


def _dirs(files) -> set[str]:
    return {f.rsplit("/", 1)[0] for f in files if "/" in f}


def score(hits: list[dict], gold_files: list[str], gold_repo: str) -> dict:
    retrieved = {f for h in hits for f in (h["props"] or {}).get("files", [])}
    gold = set(gold_files)
    gdirs, rdirs = _dirs(gold), _dirs(retrieved)
    same_repo = sum(1 for h in hits if (h["props"] or {}).get("repo") == gold_repo)
    return {
        "file_recall": len(gold & retrieved) / len(gold) if gold else 0.0,
        "dir_recall": len(gdirs & rdirs) / len(gdirs) if gdirs else 0.0,
        "same_repo_rate": same_repo / len(hits) if hits else 0.0,
        "hit_any_file": 1.0 if gold & retrieved else 0.0,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--n-queries", type=int, default=100)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--dsn", default=SWEBENCH_DSN)
    ap.add_argument("--cache-path", default=str(HERE / ".cache_joint" / "embeddings.json"))
    ap.add_argument("--out", default=str(HERE / "compare_embeddings.json"))
    args = ap.parse_args()

    df = load_dataset()
    # Every Nth row, so the sample spans repos rather than clustering in one.
    step = max(1, len(df) // args.n_queries)
    rows = [r for i, (_, r) in enumerate(df.iterrows()) if i % step == 0][: args.n_queries]
    print(f"{len(rows)} queries, top-{args.top_k}, leave-one-out by instance id")

    embedder = CachedEmbedder(min_interval=21.0, cache_path=args.cache_path)
    embedder.MAX_BATCH_TOKENS = 3300
    pool = await create_pool(dsn=args.dsn, min_size=1, max_size=4)
    per_col: dict[str, list[dict]] = {"embedding": [], "embedding_joint": []}
    detail = []

    try:
        queries = [f"{title_of(r['problem_statement'])}\n\n"
                   f"{normalize_statement(r['problem_statement'])[:1500]}" for r in rows]
        print("embedding queries (issue text only, identical for both columns)...")
        vecs = await embedder.embed(queries, input_type="query")

        for r, vec in zip(rows, vecs):
            gold_files, _ = patch_facts(str(r["patch"]))
            if not gold_files:
                continue
            rec = {"instance_id": r["instance_id"], "repo": r["repo"]}
            for col in ("embedding", "embedding_joint"):
                hits = await pool.fetch(SQL.format(col=col), to_pgvector(vec),
                                        r["instance_id"], args.top_k)
                s = score([dict(h) for h in hits], gold_files, r["repo"])
                per_col[col].append(s)
                rec[col] = s
            detail.append(rec)

        print(f"\n{'metric':<20}{'split (issue)':>16}{'joint (issue+diff)':>20}{'delta':>10}")
        summary = {}
        for metric in ("file_recall", "dir_recall", "same_repo_rate", "hit_any_file"):
            a = statistics.mean(s[metric] for s in per_col["embedding"])
            b = statistics.mean(s[metric] for s in per_col["embedding_joint"])
            summary[metric] = {"split": round(a, 4), "joint": round(b, 4),
                               "delta": round(b - a, 4)}
            print(f"{metric:<20}{a:>16.4f}{b:>20.4f}{b - a:>+10.4f}")

        # Paired sign test on hit_any_file: per-query wins in each direction.
        # Means can move while no individual query changes, so the paired
        # counts are what say whether the representation actually reordered
        # anything.
        wins = sum(1 for d in detail
                   if d["embedding_joint"]["hit_any_file"] > d["embedding"]["hit_any_file"])
        losses = sum(1 for d in detail
                     if d["embedding_joint"]["hit_any_file"] < d["embedding"]["hit_any_file"])
        print(f"\npaired on hit_any_file: joint better on {wins}, worse on {losses}, "
              f"tied on {len(detail) - wins - losses}")
        if wins + losses:
            from math import comb
            n, k = wins + losses, min(wins, losses)
            p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
            print(f"sign test p = {p:.5f} over {n} discordant queries")
            summary["sign_test"] = {"joint_better": wins, "joint_worse": losses,
                                    "p": round(p, 5)}
        else:
            summary["sign_test"] = {"joint_better": 0, "joint_worse": 0, "p": None}
            print("no query changed outcome — the representations rank identically here")

        Path(args.out).write_text(json.dumps(
            {"n": len(detail), "top_k": args.top_k, "summary": summary,
             "detail": detail}, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
