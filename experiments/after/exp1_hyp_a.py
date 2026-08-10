"""
Experiment 1, Hypothesis A -- task-level retrieval.

Given only a task's instruction text, does the system retrieve the correct
skill? Three arms over an identical library, identical query vectors (all
read from the same disk cache, so no arm can differ by embedding drift),
and identical scoring code:

  A0  lexical      -- _lexical_overlap, the fallback every vector path in
                      this codebase silently degrades to. Present as a
                      floor: any vector arm that fails to beat it is not
                      earning its API cost.
  A1  flat vector  -- exhaustive cosine over all 22 library leaves.
  A2  hierarchical -- beam descent through OWNS/PARENT_OF.

Scored on the 89 SINGLE-skill tasks, where "the correct skill" is
unambiguous. Composite tasks are reported separately and are the subject
of Hypothesis B, not this one.

A2 returns exactly one leaf by construction (SearchResult has a single
leaf_id), so it gets precision@1 and comparison counts only. Reporting a
recall@3 for it would require inventing a ranking it does not produce.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scipy.stats import binomtest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.hierarchy import hierarchical_search
from app.services.reuse_detection import _lexical_overlap
from experiments.after.corpus import load_skills, load_tasks
from experiments.after.embed_cache import CachedEmbedder

LIBRARY_CREATED_BY = "after_experiment"


@dataclass
class ArmResult:
    name: str
    hits_at_1: int = 0
    hits_at_3: int = 0
    hits_at_5: int = 0
    n: int = 0
    comparisons: list[int] = field(default_factory=list)
    per_task_hit1: dict[str, bool] = field(default_factory=dict)
    supports_ranking: bool = True
    # A2 only: descent aborted below confidence_floor and returned no leaf.
    # Without this, "declined to answer" and "answered wrongly" both show up
    # as a p@1 miss, which are different failures with different fixes.
    aborted: int = 0

    def summary(self) -> dict:
        out = {
            "arm": self.name,
            "n": self.n,
            "p@1": round(self.hits_at_1 / self.n, 4) if self.n else None,
            "mean_comparisons": round(sum(self.comparisons) / len(self.comparisons), 1)
            if self.comparisons else None,
        }
        if not self.supports_ranking:
            out["aborted_low_confidence"] = self.aborted
            answered = self.n - self.aborted
            out["p@1_when_answered"] = (
                round(self.hits_at_1 / answered, 4) if answered else None
            )
        if self.supports_ranking:
            out["recall@3"] = round(self.hits_at_3 / self.n, 4) if self.n else None
            out["recall@5"] = round(self.hits_at_5 / self.n, 4) if self.n else None
        return out


def mcnemar(a: dict[str, bool], b: dict[str, bool]) -> dict:
    """
    Exact McNemar on paired binary outcomes -- the correct test here
    because both arms answer the SAME tasks, so the samples are matched,
    not independent. (A two-sample t-test, which the repo's
    eval/statistics.py provides, assumes independent groups and would be
    the wrong tool.)
    """
    keys = sorted(set(a) & set(b))
    b_only = sum(1 for k in keys if a[k] and not b[k])   # a right, b wrong
    c_only = sum(1 for k in keys if b[k] and not a[k])   # b right, a wrong
    n_disc = b_only + c_only
    if n_disc == 0:
        return {"discordant": 0, "p_value": 1.0, "note": "arms agree on every task"}
    p = binomtest(b_only, n_disc, 0.5).pvalue
    return {
        "a_right_b_wrong": b_only,
        "b_right_a_wrong": c_only,
        "discordant": n_disc,
        "p_value": round(float(p), 6),
    }


async def leaf_library(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, name, name || ' ' || COALESCE(description,'') AS full_text "
        "FROM task_nodes WHERE created_by = $1 AND t_invalid IS NULL",
        LIBRARY_CREATED_BY,
    )
    return [dict(r) for r in rows]


async def flat_ranked(pool, query_vec: list[float], k: int = 5) -> list[str]:
    rows = await pool.fetch(
        "SELECT name, 1 - (embedding <=> $1::vector) AS sim FROM task_nodes "
        "WHERE created_by = $2 AND t_invalid IS NULL AND embedding IS NOT NULL "
        "ORDER BY sim DESC LIMIT $3",
        to_pgvector(query_vec), LIBRARY_CREATED_BY, k,
    )
    return [r["name"] for r in rows]


def lexical_ranked(instruction: str, library: list[dict], k: int = 5) -> list[str]:
    scored = sorted(
        ((_lexical_overlap(instruction, row["full_text"]), row["name"]) for row in library),
        reverse=True,
    )
    return [name for _, name in scored[:k]]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-interval", type=float, default=21.0)
    args = ap.parse_args()

    tasks = load_tasks()
    single = [t for t in tasks if len(t.gold_skills) == 1]
    composite = [t for t in tasks if t.is_composite]
    print(f"{len(single)} single-skill tasks (scored), {len(composite)} composite (reported separately)")

    pool = await create_pool(min_size=1, max_size=4)
    embedder = CachedEmbedder(min_interval=args.min_interval)
    scope = AccessScope.unrestricted()
    try:
        library = await leaf_library(pool)
        known = {r["name"] for r in library}
        print(f"library: {len(library)} leaves")
        if len(library) != len(load_skills()):
            print("FAIL: library size does not match the corpus; re-run seed_library.py")
            return 1

        a0 = ArmResult("A0_lexical")
        a1 = ArmResult("A1_flat_vector")
        a2 = ArmResult("A2_hierarchical", supports_ranking=False)

        for i, t in enumerate(single, 1):
            gold = t.gold_skills[0]
            if gold not in known:
                print(f"skip {t.task_id}: gold {gold!r} not in library")
                continue

            lex = lexical_ranked(t.instruction, library)
            a0.n += 1
            a0.hits_at_1 += lex[:1] == [gold]
            a0.hits_at_3 += gold in lex[:3]
            a0.hits_at_5 += gold in lex[:5]
            a0.comparisons.append(len(library))
            a0.per_task_hit1[t.task_id] = lex[:1] == [gold]

            qvec = await embedder.embed_one(t.instruction, input_type="query")
            flat = await flat_ranked(pool, qvec)
            a1.n += 1
            a1.hits_at_1 += flat[:1] == [gold]
            a1.hits_at_3 += gold in flat[:3]
            a1.hits_at_5 += gold in flat[:5]
            a1.comparisons.append(len(library))
            a1.per_task_hit1[t.task_id] = flat[:1] == [gold]

            res = await hierarchical_search(
                pool, "task_nodes", t.instruction, scope=scope, embedder=embedder,
                beam=3, adaptive=True,
            )
            hit = res.leaf_name == gold
            a2.n += 1
            a2.hits_at_1 += hit
            a2.aborted += res.used_flat_fallback or res.leaf_id is None
            a2.comparisons.append(res.comparisons)
            a2.per_task_hit1[t.task_id] = hit

            if i % 20 == 0:
                print(f"  {i}/{len(single)}")

        results = {
            "n_single_skill": len(single),
            "library_size": len(library),
            "arms": [a0.summary(), a1.summary(), a2.summary()],
            "tests": {
                "A1_vs_A0_mcnemar": mcnemar(a1.per_task_hit1, a0.per_task_hit1),
                "A2_vs_A1_mcnemar": mcnemar(a2.per_task_hit1, a1.per_task_hit1),
            },
            "embedder": embedder.stats(),
        }
        print("\n" + json.dumps(results, indent=2))
        Path(__file__).parent.joinpath("results_exp1_hyp_a.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print("\nwrote results_exp1_hyp_a.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
