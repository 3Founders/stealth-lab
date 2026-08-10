"""
Multi-instance graph-memory ablation, with the precedents' gold diffs shown.

WHY THIS EXISTS SEPARATELY FROM run_graph_instance.py

One instance cannot answer the question. The earlier 9-instance ansible run
produced ZERO discordant pairs -- both arms solved the same instance and
failed the same eight -- so McNemar had no input at all. That is not "no
significant difference", it is no information, and it is what n too small
looks like. This runs the same procedure over many instances and reports the
paired test at the end.

EXPERIMENT 1 (this file, default flags): coarse nodes. One task node per
issue, carrying that issue's whole gold diff. The model sees the retrieved
precedents' exact diffs.

EXPERIMENT 2 (only if 1 is not significant): per-hunk nodes, so a precedent
is "someone added a cookie-name constant to an auth middleware" rather than
"someone had an auth issue". Costs ~2.4h of embedding, which is why it is
gated on 1 coming back null rather than run speculatively.

RESUMABLE, because a run this long will be interrupted. Results append to
JSONL and completed instances are skipped, so a stop costs at most the
instance in flight -- not the whole sweep. Same reasoning as the chunked
embedding commits in graph_ingest.py, learned the same way.

EVERY INSTANCE IS GOLD-GATED. The reference patch runs first; if it does not
resolve, the instance is excluded before either arm spends a token. NodeBB in
the pilot is exactly this case -- its gold patch passes all 3 f2p and then
breaks 6 unrelated tests. Including such a row adds a guaranteed double loss
that drags both arms toward zero and hides real differences.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE.parents[1]))

from app.config import settings  # noqa: E402
from app.db.session import create_pool  # noqa: E402
from experiments.after.embed_cache import CachedEmbedder  # noqa: E402
from agent import Agent, RepoSandbox  # noqa: E402
from htn_agent import AugmentedHTNAgent as HTNAgent  # noqa: E402

# Arm specification: name -> (agent kind, whether the memory block is shown).
#
# Three arms, giving TWO clean paired comparisons on identical instances:
#   no_memory   vs graph_memory  -> does the knowledge graph help?      (flat)
#   graph_memory vs htn_memory   -> does HTN decomposition help?  (memory held fixed)
#
# Every arm shares the instance, the snapshot, the tools and the step budget,
# so each comparison varies exactly one thing. A fourth arm (htn_no_memory)
# would complete the 2x2 but at ~8 min per arm it would cut the instance
# count by a quarter, and n matters more here than the interaction term --
# at these resolution rates the experiment is already power-limited.
ARM_SPEC = {
    "no_memory":    ("flat", False),
    "graph_memory": ("flat", True),
    "htn_memory":   ("htn", True),
}
from graph_ingest import (  # noqa: E402
    SWEBENCH_DSN, load_dataset, normalize_statement, patch_facts, title_of,
)
from graph_memory import (  # noqa: E402
    hold_out, htn_route, rebuild_hierarchy, render_context, restore_all, retrieve,
)
from pro_harness import evaluate, image_for, pull_image, remove_image  # noqa: E402
from run_experiment import extract, snapshot_repo  # noqa: E402
from run_graph_instance import (  # noqa: E402
    DEFAULT_SCRIPTS, _hand_editable, score_copyability, score_retrieval,
)


def select_instances(df, n: int, scripts_dir: str, seed: int = 0) -> list:
    """
    Instances that are winnable in principle and cheap to grade.

    Filters, each with a reason:
      - a run_script must exist, or the harness cannot grade it at all
      - the repo's gold patch resolved in the pilot (NodeBB excluded)
      - every gold file is hand-editable: a patch that regenerates protobuf
        stubs or re-resolves a lockfile is a `make generate` task, and an
        agent fails it regardless of retrieval quality, so the outcome
        carries no information about what is being measured
      - 1-4 gold files, to keep grading time bounded

    Round-robin across repos rather than taking the first n, so the result
    is not a statement about one repo's conventions.
    """
    have = set(os.listdir(scripts_dir))
    pilot = HERE / "pilot_gold_results.json"
    verified = set()
    if pilot.exists():
        verified = {r["repo"] for r in json.loads(pilot.read_text())["results"]
                    if r["resolved"]}

    by_repo: dict[str, list] = {}
    for _, row in df.iterrows():
        if row["instance_id"] not in have:
            continue
        if verified and row["repo"] not in verified:
            continue
        files, _ = patch_facts(str(row["patch"]))
        if not files or _hand_editable(files) != files or len(files) > 4:
            continue
        by_repo.setdefault(row["repo"], []).append(row)

    picked, repos = [], sorted(by_repo)
    i = 0
    while len(picked) < n and any(by_repo.values()):
        repo = repos[i % len(repos)]
        if by_repo.get(repo):
            picked.append(by_repo[repo].pop(0))
        i += 1
        if i > len(repos) * (n + 5):
            break
    return picked[:n]


def load_done(path: Path, arms: Optional[list[str]] = None) -> set[str]:
    """
    Instances that need not be re-run.

    A row counts as done only if it actually produced BOTH arms. Rows that
    recorded a harness error or a holdout leak are deliberately NOT counted:
    keying on instance_id alone meant a transient failure was baked in
    permanently and never retried on resume, which is how
    graph_experiment_1.jsonl ended up with api_error rows frozen into it. A
    gold-excluded row IS done -- its gold patch does not resolve, and that
    will not change on a retry.
    """
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        iid = rec.get("instance_id")
        if not iid:
            continue
        if rec.get("error"):
            continue  # transient: retry on resume
        if rec.get("excluded") or (arms and all(rec.get(a) for a in arms)):
            done.add(iid)
    return done


def mcnemar(a_only: int, b_only: int) -> tuple[float, str]:
    """
    Exact binomial McNemar on the DISCORDANT pairs only.

    Concordant pairs carry no information about a difference -- an instance
    both arms solve, or both fail, is consistent with any effect size. With
    zero discordant pairs there is no test to run, and reporting p=1.0 there
    would imply evidence of no difference when there is simply no evidence.
    """
    n = a_only + b_only
    if n == 0:
        return float("nan"), "no discordant pairs — the test has no input"
    from math import comb
    k = min(a_only, b_only)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))
    return p, f"{n} discordant pairs (no_mem-only {a_only}, mem-only {b_only})"


async def run_one(sample, pool, embedder, agent, htn, args) -> dict:
    iid = sample["instance_id"]
    gold_files, _ = patch_facts(str(sample["patch"]))
    title = title_of(sample["problem_statement"])
    rec: dict = {"instance_id": iid, "repo": sample["repo"],
                 "language": sample["repo_language"], "title": title,
                 "gold_files": gold_files, "model": args.model,
                 "max_steps": args.max_steps,
                 "arm_config": {"include_patches": args.include_patches,
                                "include_requirements": args.include_requirements}}
    t_start = time.time()

    await hold_out(pool, iid)
    await rebuild_hierarchy(pool, embedder)
    query = f"{title}\n\n{normalize_statement(sample['problem_statement'])[:1500]}"
    hits, diag = await retrieve(pool, query, embedder, top_k=args.top_k,
                                embedding_column=args.embedding_column)
    rec["embedding_column"] = args.embedding_column
    rec["htn"] = await htn_route(pool, query, embedder)

    if any(h.instance_id == iid for h in hits):
        rec["error"] = "holdout_leaked"
        return rec

    rec["retrieval"] = score_retrieval(hits, gold_files, sample["repo"])
    rec["retrieval_diag"] = diag
    rec["copyability"] = score_copyability(hits, str(sample["patch"]))
    memory_block = render_context(
        hits, max_chars=args.memory_chars, include_patches=args.include_patches,
        patch_chars=args.patch_chars, include_requirements=args.include_requirements,
        minimal=args.minimal_context)
    rec["memory_block_chars"] = len(memory_block)

    inst_dir = os.path.join(args.work_dir, iid)
    pull_image(image_for(sample))
    try:
        tar_path = snapshot_repo(sample, os.path.join(inst_dir, "snap"))
        gold = evaluate(sample, str(sample["patch"]), args.scripts_dir,
                        os.path.join(inst_dir, "gold_ws"), keep_image=True)
        rec["gold"] = {"resolved": gold.resolved, "status": gold.status,
                       "n_tests_parsed": gold.n_tests_parsed}
        if not gold.resolved:
            rec["excluded"] = "gold_patch_does_not_resolve"
            return rec

        for arm in args.arms:
            kind, use_memory = ARM_SPEC[arm]
            work = extract(tar_path, os.path.join(inst_dir, f"repo_{arm}"))
            sandbox = RepoSandbox(work)
            runner = agent if kind == "flat" else htn
            run = runner.run(sample, sandbox, arm,
                             memory_block=memory_block if use_memory else "")
            if run.patch.strip():
                res = evaluate(sample, run.patch, args.scripts_dir,
                               os.path.join(inst_dir, f"{arm}_ws"), keep_image=True)
                resolved, status = res.resolved, res.status
                graded = {"f2p_passed": len(res.f2p_passed),
                          "f2p_missing": len(res.f2p_missing),
                          "p2p_broke": len(res.p2p_broke),
                          "apply_status": res.apply_status}
            else:
                resolved, status, graded = False, "no_patch", {}
            # Heartbeat AS EACH ARM FINISHES, not after the whole instance.
            # Without it the log is silent from "[n/20]" until all three arms
            # complete -- 30-45 minutes -- which is indistinguishable from a
            # hang. Two runs were killed on that misreading; the silence was
            # the bug, not the process.
            print(f"      · {arm:<13} {'RESOLVED' if resolved else status:<12} "
                  f"tok={run.usage.total:,} tools={len(run.tool_calls)} "
                  f"{run.wall_seconds:.0f}s", flush=True)
            invalid = run.stop_reason == "api_error"
            rec[arm] = {
                "valid": not invalid,
                # The REASON, not just the flag. Previously only
                # run_graph_instance.py wrote this, so every invalid row in
                # the existing result files carries valid=False with
                # invalid_reason=None -- the fact survives, the explanation
                # does not, and months later nobody can tell a provider kill
                # from any other exclusion.
                "invalid_reason": "provider_error_truncated_episode" if invalid else None,
                "resolved": resolved, "status": status,
                "total_tokens": run.usage.total, "llm_calls": run.usage.calls,
                "n_tool_calls": len(run.tool_calls),
                # The full sequence, not just the count. `no_patch` has two
                # completely different causes that only the sequence can tell
                # apart: the agent never calling edit_file at all, versus
                # calling it repeatedly and having every attempt rejected for
                # a byte-mismatch in old_str. The first is a budget/prompting
                # problem, the second a tolerance problem in the edit tool,
                # and they need opposite fixes. Dropping this field made the
                # earlier runs undiagnosable.
                "tool_calls": run.tool_calls,
                "n_edit_attempts": run.tool_calls.count("edit_file"),
                # How many edits only landed via the whitespace-tolerant
                # fallback. The counter existed but nothing read it, so there
                # was no way to tell how often the model mis-indents -- which
                # is exactly the signal that says whether that fallback is
                # carrying the run or is dead weight.
                "tolerant_edits": sandbox.tolerant_edits,
                "n_files_created": sum(1 for k, v in sandbox._original.items() if v is None),
                "n_files_deleted": len(sandbox._deleted),
                "agent_kind": kind,
                "htn": getattr(run, "htn", None),
                "n_recovered": run.tool_calls.count("__recovered__"),
                "files_edited": run.files_edited,
                "files_edited_correct": sorted(set(run.files_edited) & set(gold_files)),
                "patch_bytes": len(run.patch), "stop_reason": run.stop_reason,
                "wall_seconds": round(run.wall_seconds, 1), "agent_error": run.error,
                "graded": graded,
            }
            shutil.rmtree(work, ignore_errors=True)
    finally:
        remove_image(image_for(sample))
        shutil.rmtree(inst_dir, ignore_errors=True)
        rec["wall_seconds"] = round(time.time() - t_start, 1)
    return rec


def summarise(rows: list[dict], arms: Optional[list[str]] = None) -> dict:
    """
    Per-arm rates plus a PAIRWISE McNemar for every arm pair.

    Pairwise, not one comparison, because with three arms the interesting
    quantities are different questions: no_memory vs graph_memory asks whether
    the graph helps, graph_memory vs htn_memory asks whether decomposition
    helps with memory held fixed. Collapsing them into one number would
    answer neither.

    An instance counts only if EVERY arm produced a valid run on it -- a row
    where one arm was killed by the provider cannot contribute a paired
    comparison, and silently dropping only that arm would compare different
    instance sets to each other.
    """
    arms = arms or [a for a in ARM_SPEC if any(a in r for r in rows)]
    usable = [r for r in rows
              if all(r.get(a, {}).get("valid") for a in arms)]
    n = len(usable)

    per_arm = {}
    for a in arms:
        res = sum(1 for r in usable if r[a]["resolved"])
        per_arm[a] = {
            "resolved": res,
            "rate": round(res / n, 3) if n else None,
            "tokens": sum(r[a]["total_tokens"] for r in usable),
            "no_patch": sum(1 for r in usable if r[a].get("status") == "no_patch"),
            "mean_tool_calls": round(
                sum(r[a]["n_tool_calls"] for r in usable) / n, 1) if n else None,
        }

    pairs = {}
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            xo = sum(1 for r in usable if r[x]["resolved"] and not r[y]["resolved"])
            yo = sum(1 for r in usable if r[y]["resolved"] and not r[x]["resolved"])
            p, note = mcnemar(xo, yo)
            tx = sum(r[x]["total_tokens"] for r in usable) or 1
            ty = sum(r[y]["total_tokens"] for r in usable)
            pairs[f"{x} vs {y}"] = {
                "discordant_first_only": xo, "discordant_second_only": yo,
                "mcnemar_p": None if p != p else round(p, 5), "note": note,
                "token_delta_pct": round(100 * (ty - tx) / tx, 1),
            }

    cop = [r["copyability"]["max_copyable_fraction"] for r in usable
           if r.get("copyability", {}).get("max_copyable_fraction") is not None]
    fr = [r["retrieval"]["file_recall"] for r in usable
          if r.get("retrieval", {}).get("file_recall") is not None]
    dr = [r["retrieval"]["dir_recall"] for r in usable
          if r.get("retrieval", {}).get("dir_recall") is not None]
    htn_rows = [r["htn_memory"]["htn"] for r in usable
                if r.get("htn_memory", {}).get("htn")]
    return {
        "n_total": len(rows), "n_usable": n,
        "n_excluded_gold": sum(1 for r in rows if r.get("excluded")),
        "arms": per_arm, "pairwise": pairs,
        "mean_max_copyable_fraction": round(sum(cop) / len(cop), 4) if cop else None,
        "instances_with_any_copyable_line": sum(1 for c in cop if c > 0),
        "mean_file_recall": round(sum(fr) / len(fr), 3) if fr else None,
        "mean_dir_recall": round(sum(dr) / len(dr), 3) if dr else None,
        "htn": {
            "mean_subgoals": round(sum(len(h["plan"]) for h in htn_rows) / len(htn_rows), 2),
            "total_replans": sum(h["replans"] for h in htn_rows),
            "subgoals_done": sum(h["subgoals_done"] for h in htn_rows),
            "subgoals_failed": sum(h["subgoals_failed"] for h in htn_rows),
            "decompose_failures": sum(1 for h in htn_rows if h["decompose_failed"]),
        } if htn_rows else None,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--n-instances", type=int, default=20)
    ap.add_argument("--instance-ids", default=None,
                    help="comma-separated instance ids to run instead of the "
                         "standard selection. Use when a second run must not "
                         "collide with one already in flight: both runs pull an "
                         "image and remove_image() it in a finally, so two "
                         "processes on the SAME instance will have one delete "
                         "the image the other is still using.")
    ap.add_argument("--ids-from", default=None,
                    help="path to a results JSONL; run exactly the instances it "
                         "has already completed. Safe to run alongside the job "
                         "that produced it, and directly comparable to it.")
    ap.add_argument("--dsn", default=SWEBENCH_DSN)
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS)
    ap.add_argument("--work-dir", default=os.path.expanduser("~/AppData/Local/Temp/swebench_graph"))
    ap.add_argument("--model", default="gemma-4-31B-it")
    ap.add_argument("--max-steps", type=int, default=28)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--include-patches", action="store_true", default=True)
    ap.add_argument("--no-include-patches", dest="include_patches", action="store_false")
    ap.add_argument("--include-requirements", action="store_true", default=True)
    ap.add_argument("--minimal-context", action="store_true", default=True,
                    help="render ONLY the issue title, files, and the change as "
                         "SEARCH/REPLACE blocks -- no symbol lists, area tags or "
                         "requirements prose. Those describe the fix; the block IS "
                         "the fix, and every extra line is resent on every call.")
    ap.add_argument("--full-context", dest="minimal_context", action="store_false")
    ap.add_argument("--patch-chars", type=int, default=900)
    ap.add_argument("--memory-chars", type=int, default=3500)
    ap.add_argument("--embedding-column", default="embedding_joint",
                    choices=["embedding", "embedding_joint"])
    ap.add_argument("--arms", default="no_memory,graph_memory,htn_memory",
                    help="comma-separated arms from ARM_SPEC, run per instance")
    ap.add_argument("--out", default=str(HERE / "graph_experiment_joint.jsonl"))
    args = ap.parse_args()
    args.arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    unknown = [a for a in args.arms if a not in ARM_SPEC]
    if unknown:
        raise SystemExit(f'unknown arms: {unknown}; choose from {list(ARM_SPEC)}')

    out = Path(args.out)
    df = load_dataset()
    wanted: list[str] = []
    if args.instance_ids:
        wanted = [s.strip() for s in args.instance_ids.split(",") if s.strip()]
    elif args.ids_from:
        src = Path(args.ids_from)
        wanted = [r["instance_id"] for r in
                  (json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip())
                  if r.get('no_memory')]  # completed rows only, not excluded/errored
    if wanted:
        by_id = {r["instance_id"]: r for _, r in df.iterrows()}
        picked = [by_id[i] for i in wanted if i in by_id][: args.n_instances]
        print(f"restricted to {len(picked)} explicitly-named instances")
    else:
        picked = select_instances(df, args.n_instances, args.scripts_dir)
    done = load_done(out, args.arms)
    print(f"experiment: include_patches={args.include_patches} "
          f"include_requirements={args.include_requirements} model={args.model}")
    print(f"{len(picked)} instances selected, {len(done)} already done")
    from collections import Counter
    print("by repo:", dict(Counter(s["repo"] for s in picked)))

    embedder = CachedEmbedder(min_interval=21.0)
    embedder.MAX_BATCH_TOKENS = 3300
    from openai import OpenAI
    # max_retries=0: the SDK's OWN retry loop must be OFF.
    # Diagnosed from a live stack dump of a run that produced 0 rows in 53
    # minutes -- it sat in openai/_base_client.py:_sleep_for_retry. `timeout`
    # bounds ONE http attempt; the SDK then sleeps and retries OUTSIDE that
    # timeout, and only after exhausting its own retries does our handler see
    # an exception and start OUR retry loop. The two nest multiplicatively.
    # One bounded retry policy, in Agent._chat, where it can be reasoned about.
    client = OpenAI(max_retries=0, api_key=settings.require("general_compute_api_key"),
                    base_url=settings.general_compute_base_url)
    agent = Agent(client, args.model, max_steps=args.max_steps)
    htn = HTNAgent(client, args.model, max_steps=args.max_steps)
    pool = await create_pool(dsn=args.dsn, min_size=1, max_size=4)

    try:
        await restore_all(pool)
        for i, sample in enumerate(picked, 1):
            iid = sample["instance_id"]
            if iid in done:
                print(f"[{i}/{len(picked)}] skip (done) {iid[:56]}")
                continue
            print(f"\n[{i}/{len(picked)}] {sample['repo']} — {title_of(sample['problem_statement'])[:70]}",
                  flush=True)
            try:
                rec = await run_one(sample, pool, embedder, agent, htn, args)
            except Exception as exc:  # noqa: BLE001
                # Full traceback, not just the message. A bare
                # "ValueError: Paths don't have the same drive" names neither
                # the file nor the call, and an unattended run that logs only
                # that is unfixable without reproducing it.
                import traceback
                tb = traceback.format_exc()
                rec = {"instance_id": iid, "repo": sample["repo"],
                       "error": f"{type(exc).__name__}: {exc}"[:300],
                       "traceback": tb[-2000:]}
                print("    error: " + rec["error"], flush=True)
                print(tb, flush=True)
            finally:
                await restore_all(pool)
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            if rec.get("excluded"):
                print(f"    EXCLUDED: {rec['excluded']}", flush=True)
            else:
                for a in args.arms:
                    v = rec.get(a)
                    if not v:
                        continue
                    extra = ""
                    if v.get("htn"):
                        h = v["htn"]
                        extra = (f"  subgoals={h['subgoals_done']}/{len(h['plan'])}"
                                 f" replans={h['replans']}")
                    print(f"    {a:<13}: {'RESOLVED' if v['resolved'] else 'no':>8} "
                          f"[{v['status']}] tok={v['total_tokens']:,}{extra}", flush=True)

        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        summary = summarise(rows, args.arms)
        print("\n" + "=" * 66)
        for k, v in summary.items():
            print(f"{k:<34}{v}")
        Path(str(out).replace(".jsonl", "_summary.json")).write_text(
            json.dumps(summary, indent=2), encoding="utf-8")
        return 0
    finally:
        await restore_all(pool)
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
