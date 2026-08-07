"""
Experiment 1: retrieval evaluation against the ingested AFTER skill
library (task_nodes, one per skill, already seeded via
ingest_after_skills.py).

Scope, stated plainly: this tests RETRIEVAL RANKING -- given a task's
whole instruction text, does the library rank the correct skill(s)
(task.toml's `skills` list, ground truth) above what a lexical
baseline would rank. It does NOT run the full decompose() pipeline or
invoke resolve_subtask_reuse's per-generated-op matching -- AFTER
gives us a task instruction plus a ground-truth skill LIST, not
separate per-subtask query text, so there's nothing for Part C's
per-op mechanism to run against without first generating candidate
subtasks via a real LLM call (a separate, heavier follow-up, not
this script). What this DOES test is a fair, real proxy for both
hypotheses:
  - Hypothesis A (task-level): precision@1 -- is the single best-
    ranked skill actually one of the ground-truth skills.
  - Hypothesis B (composite/subtask-level): recall@k for composite
    tasks specifically -- are ALL ground-truth skills present in the
    top-k ranked results, k scaled to how many skills the task
    actually needs.

Batched throughout, same principle as everywhere else in this
project: ONE embed() call for all task instructions (not one per
task -- this is what avoids repeating the free-tier rate-limit
throttling seen during backfill_embeddings.py), and ONE SQL cross-join
round trip scoring every task against every skill (same pattern as
hierarchy.py's batch_hierarchical_search) rather than one query per
task.

Run from backend/, with .env configured and after_task_index.json
(from index_after_tasks.py) present:
    python scripts/run_experiment_1.py
    python scripts/run_experiment_1.py --pilot 8   # first N tasks only, for a quick sanity check
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from huggingface_hub import hf_hub_download

from app.db.session import create_pool
from app.services.embeddings import Embedder, to_pgvector
from app.services.reuse_detection import _lexical_overlap

REPO = "DavydenkoGr/AFTER"


async def fetch_instructions(tasks: list[dict]) -> dict[str, str]:
    """Download instruction.md for each task. HF downloads, not Voyage
    calls -- not subject to the embedding rate limit, but still N
    separate HTTP requests, so this is the slow part wall-clock-wise,
    not the embedding step."""
    texts = {}
    for i, t in enumerate(tasks):
        instr_path = t["path"].replace("task.toml", "instruction.md")
        try:
            local = hf_hub_download(REPO, filename=instr_path, repo_type="dataset")
            texts[t["id"]] = open(local, encoding="utf-8").read()
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not fetch instruction for {t['id']}: {exc}")
        if (i + 1) % 20 == 0:
            print(f"  fetched {i + 1}/{len(tasks)} instructions")
    return texts


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", type=int, default=None, help="only run the first N tasks")
    parser.add_argument("--index", default="after_task_index.json")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    tasks = json.load(open(args.index))
    if args.pilot:
        tasks = tasks[: args.pilot]
    print(f"running experiment 1 on {len(tasks)} tasks")

    print("\n[1/4] fetching instruction.md for each task...")
    instructions = await fetch_instructions(tasks)
    tasks = [t for t in tasks if t["id"] in instructions]
    print(f"  {len(tasks)} tasks with a readable instruction")

    print("\n[2/4] batch-embedding all task instructions (one call)...")
    embedder = Embedder()
    vectors = await embedder.embed([instructions[t["id"]] for t in tasks], input_type="query")
    print(f"  embedded {len(vectors)} tasks")

    print("\n[3/4] scoring all tasks against the skill library (one SQL round trip)...")
    pool = await create_pool(os.environ["DATABASE_URL"])
    pairs = [(t["id"], to_pgvector(v)) for t, v in zip(tasks, vectors)]
    rows = await pool.fetch(
        "SELECT q.task_id, n.skill_ref, "
        "1 - (n.embedding <=> q.vec_text::vector) AS similarity "
        "FROM unnest($1::text[], $2::text[]) AS q(task_id, vec_text) "
        "CROSS JOIN task_nodes n "
        "WHERE n.t_invalid IS NULL AND n.skill_ref IS NOT NULL",
        [p[0] for p in pairs], [p[1] for p in pairs],
    )

    # also fetch skill name/description text for the lexical baseline
    skill_rows = await pool.fetch(
        "SELECT skill_ref, name, description FROM task_nodes "
        "WHERE t_invalid IS NULL AND skill_ref IS NOT NULL"
    )
    skill_text = {r["skill_ref"]: f"{r['name']} {r['description'] or ''}" for r in skill_rows}
    await pool.close()

    ranked_by_task: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        ranked_by_task.setdefault(r["task_id"], []).append((r["skill_ref"], float(r["similarity"])))
    for tid in ranked_by_task:
        ranked_by_task[tid].sort(key=lambda x: -x[1])

    print("\n[4/4] scoring lexical baseline and computing metrics...")
    lexical_ranked_by_task: dict[str, list[tuple[str, float]]] = {}
    for t in tasks:
        instr = instructions[t["id"]]
        scored = [(skill, _lexical_overlap(instr, text)) for skill, text in skill_text.items()]
        scored.sort(key=lambda x: -x[1])
        lexical_ranked_by_task[t["id"]] = scored

    # --- Hypothesis A: precision@1 ---
    vec_p1_hits, lex_p1_hits = 0, 0
    for t in tasks:
        gt = set(t["skills"])
        vec_top1 = ranked_by_task.get(t["id"], [(None, 0)])[0][0]
        lex_top1 = lexical_ranked_by_task[t["id"]][0][0]
        vec_p1_hits += int(vec_top1 in gt)
        lex_p1_hits += int(lex_top1 in gt)

    n = len(tasks)
    print(f"\n=== Hypothesis A: precision@1 (n={n}) ===")
    print(f"  our retrieval (vector): {vec_p1_hits}/{n} = {vec_p1_hits/n*100:.1f}%")
    print(f"  lexical baseline:       {lex_p1_hits}/{n} = {lex_p1_hits/n*100:.1f}%")

    # --- Hypothesis B: recall@k on composite tasks ---
    composite = [t for t in tasks if len(t["skills"]) > 1]
    if composite:
        vec_recalls, lex_recalls = [], []
        for t in composite:
            gt = set(t["skills"])
            k = len(gt)
            vec_topk = {s for s, _ in ranked_by_task.get(t["id"], [])[:k]}
            lex_topk = {s for s, _ in lexical_ranked_by_task[t["id"]][:k]}
            vec_recalls.append(len(gt & vec_topk) / len(gt))
            lex_recalls.append(len(gt & lex_topk) / len(gt))

        print(f"\n=== Hypothesis B: recall@k on composite tasks (n={len(composite)}, k=|ground truth skills|) ===")
        print(f"  our retrieval (vector): {sum(vec_recalls)/len(vec_recalls)*100:.1f}% mean recall")
        print(f"  lexical baseline:       {sum(lex_recalls)/len(lex_recalls)*100:.1f}% mean recall")

        exact_vec = sum(1 for r in vec_recalls if r == 1.0)
        exact_lex = sum(1 for r in lex_recalls if r == 1.0)
        print(f"  exact full-set matches -- vector: {exact_vec}/{len(composite)}, lexical: {exact_lex}/{len(composite)}")

    # --- per-role breakdown, since roles differ a lot in size ---
    print(f"\n=== precision@1 by role ===")
    by_role: dict[str, list] = {}
    for t in tasks:
        by_role.setdefault(t["role"], []).append(t)
    for role, role_tasks in sorted(by_role.items()):
        hits = sum(1 for t in role_tasks if ranked_by_task.get(t["id"], [(None, 0)])[0][0] in set(t["skills"]))
        print(f"  {role}: {hits}/{len(role_tasks)} = {hits/len(role_tasks)*100:.1f}%")

    with open("experiment_1_results.json", "w") as fh:
        json.dump({
            "n_tasks": n,
            "vector_precision_at_1": vec_p1_hits / n,
            "lexical_precision_at_1": lex_p1_hits / n,
            "per_task_rankings": {tid: ranked_by_task.get(tid, []) for tid in [t["id"] for t in tasks]},
        }, fh, indent=2)
    print("\nwrote experiment_1_results.json")


if __name__ == "__main__":
    asyncio.run(main())
