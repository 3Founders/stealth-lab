"""
Single-instance driver for the two neuro-symbolic agent variants
(symbolic_htn_agent.py) against the real backend graph. Mirrors
run_graph_instance.py's structure exactly -- see that file for the fuller
explanation of the hold-out / rebuild / retrieve / grade / restore shape.

SAFETY. Opens with a live-run guard. graph_memory.restore_all() un-
invalidates EVERY held-out row in the database, not just this instance's,
and rebuild_hierarchy() drops and rebuilds the whole HTN tree -- both
scoped to the whole database. Running either from a second process while a
real sweep (run_graph_experiment.py) is mid-instance can corrupt its
results. This script refuses to run its holdout/rebuild path when it
detects a live run_graph_experiment process, unless --force is passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE.parents[1]))

from app.services.access import AccessScope  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from db_connect import connect_pool  # noqa: E402
from app.services.embed_cache import CachedEmbedder  # noqa: E402
from agent import RepoSandbox, Usage  # noqa: E402
from decomposition_bridge import build_decomposer  # noqa: E402
from graph_ingest import (  # noqa: E402
    SWEBENCH_DSN, load_dataset, normalize_statement, patch_facts, title_of,
)
from graph_memory import (  # noqa: E402
    hold_out, htn_route, rebuild_hierarchy, render_context, restore_all, retrieve,
)
from pro_harness import evaluate, image_for, pull_image, remove_image  # noqa: E402
from run_experiment import extract, snapshot_repo  # noqa: E402
from run_graph_instance import DEFAULT_SCRIPTS, pick_instance, score_copyability, score_retrieval  # noqa: E402
from symbolic_htn_agent import (  # noqa: E402
    NeuroSymbolicWrapperHTNAgent, TypedPreconditionHTNAgent, touch_tags_from_run,
)

AGENT_BUILDERS = {
    "htn_wrapper": NeuroSymbolicWrapperHTNAgent,
    "htn_typed": TypedPreconditionHTNAgent,
}


def _live_run_in_progress() -> tuple[bool, str]:
    """
    True if another run_graph_experiment process looks active -- same
    detection check_results.py's in_flight() already uses, duplicated here
    (not imported) since it is one small, self-contained check and
    check_results.py otherwise only exposes a printing, not a boolean.
    Fails SAFE: if the check itself cannot run, assume a live run may be in
    progress rather than silently proceeding.
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object {$_.CommandLine -like '*run_graph_experiment*'} | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=20)
        n = int((proc.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return True, "could not check for a live process -- assuming one may be running"
    if n > 0:
        return True, f"{n} run_graph_experiment process(es) currently running"
    return False, "no live run_graph_experiment process detected"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", default=None)
    ap.add_argument("--arm", default="htn_wrapper", choices=list(AGENT_BUILDERS))
    ap.add_argument("--dsn", default=SWEBENCH_DSN)
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS)
    ap.add_argument("--work-dir", default=os.path.expanduser(
        "~/AppData/Local/Temp/swebench_symbolic"))
    ap.add_argument("--model", default="gemma-4-31B-it")
    ap.add_argument("--max-steps", type=int, default=28)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--strict-callgraph-gate", action="store_true",
                    help="htn_typed only: enable the narrow hard gate, off by default")
    ap.add_argument("--with-critique", action="store_true",
                    help="decomposition_bridge: also run the adversarial critic "
                         "(roughly doubles this bridge's LLM calls)")
    ap.add_argument("--persist-on-success", action="store_true",
                    help="write this run's plan back to the method library if it resolves")
    ap.add_argument("--force", action="store_true",
                    help="skip the live-run guard -- only once you've confirmed no "
                         "other run_graph_experiment process is actually mid-instance")
    ap.add_argument("--out", default=str(HERE / "symbolic_instance_result.json"))
    args = ap.parse_args()

    in_progress, reason = _live_run_in_progress()
    if in_progress and not args.force:
        print(f"REFUSING to run: {reason}.")
        print("This script's holdout/rebuild-hierarchy steps are scoped to the whole "
              "database, not one instance, and can corrupt a real sweep's results if "
              "run concurrently with it. Wait for it to finish, or pass --force once "
              "you've confirmed it's actually idle.")
        return 1
    if in_progress:
        print(f"WARNING: {reason} -- proceeding anyway because --force was passed.")

    df = load_dataset()
    sample, selection = pick_instance(df, args.instance_id)
    iid = sample["instance_id"]
    gold_files, gold_symbols = patch_facts(str(sample["patch"]))
    title = title_of(sample["problem_statement"])
    print(f"instance : {iid}")
    print(f"repo     : {sample['repo']} ({sample['repo_language']})")
    print(f"title    : {title[:100]}")
    print(f"arm      : {args.arm}")
    print(f"selection: {selection}")

    embedder = CachedEmbedder(min_interval=21.0)
    embedder.MAX_BATCH_TOKENS = 3300
    pool = await connect_pool(args.dsn, min_size=1, max_size=4)
    record: dict = {"instance_id": iid, "repo": sample["repo"], "arm": args.arm,
                    "language": sample["repo_language"], "title": title,
                    "gold_files": gold_files, "model": args.model,
                    "max_steps": args.max_steps, "selection": selection}

    try:
        await restore_all(pool)
        n = await hold_out(pool, iid)
        print(f"\nheld out {n} rows")
        record["held_out_rows"] = n

        print("rebuilding HTN tree over the remaining leaves...")
        t0 = time.time()
        reports = await rebuild_hierarchy(pool, embedder)
        print(f"  built in {time.time()-t0:.1f}s")
        record["hierarchy"] = {k: str(v) for k, v in reports.items()}

        query = f"{title}\n\n{normalize_statement(sample['problem_statement'])[:1500]}"
        hits, diag = await retrieve(pool, query, embedder, top_k=args.top_k)
        route = await htn_route(pool, query, embedder)
        if any(h.instance_id == iid for h in hits) or route.get("instance_id") == iid:
            print("FATAL: the held-out instance retrieved ITSELF -- t_invalid is "
                  "not being honoured somewhere. Aborting.")
            record["error"] = "holdout_leaked"
            Path(args.out).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
            return 1

        record["retrieval"] = score_retrieval(hits, gold_files, sample["repo"])
        record["copyability"] = score_copyability(hits, str(sample["patch"]))
        memory_block = render_context(hits, minimal=True)
        record["memory_block_chars"] = len(memory_block)

        inst_dir = os.path.join(args.work_dir, iid)
        pull_image(image_for(sample))
        try:
            tar_path = snapshot_repo(sample, os.path.join(inst_dir, "snap"))
            gold = evaluate(sample, str(sample["patch"]), args.scripts_dir,
                            os.path.join(inst_dir, "gold_ws"), keep_image=True)
            record["gold"] = {"resolved": gold.resolved, "status": gold.status}
            print(f"\ngold: {'RESOLVED' if gold.resolved else 'FAILED - instance unusable'} "
                  f"[{gold.status}]")
            if not gold.resolved:
                record["excluded"] = "gold_patch_does_not_resolve"
                return 1

            from openai import OpenAI

            from app.config import settings
            client = OpenAI(max_retries=0, api_key=settings.require("general_compute_api_key"),
                            base_url=settings.general_compute_base_url)

            usage = Usage()   # accumulates the decomposition bridge's own token spend
            agent_cls = AGENT_BUILDERS[args.arm]
            kwargs = {"max_steps": args.max_steps}
            if args.arm == "htn_typed":
                kwargs["strict_callgraph_gate"] = args.strict_callgraph_gate
            agent = agent_cls(client, args.model, **kwargs)

            work = extract(tar_path, os.path.join(inst_dir, f"repo_{args.arm}"))
            sandbox = RepoSandbox(work)

            # Real retriever, same construction graph_memory.py uses -- this
            # is what makes decompose_issue's reuse checks (hierarchy.py,
            # reuse_detection.py, subtask_reuse.py) search the actual
            # SWE-bench-ingested corpus rather than finding nothing to reuse.
            retriever = HybridRetriever(
                pool, embedder=embedder, scope=AccessScope.unrestricted(),
                embedding_column="embedding", tables=("task_nodes", "knowledge_nodes"))
            decomposer = build_decomposer(args.model, retriever, usage,
                                          with_critique=args.with_critique)

            if args.arm == "htn_wrapper":
                synthesized = await agent._synthesize_plan(
                    pool, embedder, sample, decomposer=decomposer)
            else:
                synthesized = await agent._synthesize_plan(
                    pool, embedder, sample, sandbox, decomposer=decomposer)
            record["seeded_from_bridge_or_library"] = synthesized
            record["bridge_usage_tokens"] = usage.total

            run = agent.run(sample, sandbox, args.arm, memory_block=memory_block)
            # Fold the bridge's own LLM spend into the run's reported usage,
            # so this arm's total token cost stays comparable to the other
            # arms' -- decomposition_bridge.UsageTrackingOpenAIAgent captures
            # it separately since it runs BEFORE .run() starts its own Usage.
            run.usage.prompt_tokens += usage.prompt_tokens
            run.usage.completion_tokens += usage.completion_tokens
            run.usage.calls += usage.calls

            if run.patch.strip():
                res = evaluate(sample, run.patch, args.scripts_dir,
                               os.path.join(inst_dir, f"{args.arm}_ws"), keep_image=True)
                resolved, status = res.resolved, res.status
                graded = {"f2p_passed": len(res.f2p_passed), "f2p_missing": len(res.f2p_missing),
                          "p2p_broke": len(res.p2p_broke), "apply_status": res.apply_status}
            else:
                resolved, status, graded = False, "no_patch", {}

            record[args.arm] = {
                "resolved": resolved, "status": status,
                "total_tokens": run.usage.total, "llm_calls": run.usage.calls,
                "n_tool_calls": len(run.tool_calls), "tool_calls": run.tool_calls,
                "files_edited": run.files_edited,
                "files_edited_correct": sorted(set(run.files_edited) & set(gold_files)),
                "stop_reason": run.stop_reason, "patch_bytes": len(run.patch),
                "wall_seconds": round(run.wall_seconds, 1), "agent_error": run.error,
                "graded": graded, "htn": getattr(run, "htn", None),
            }
            print(f"\n{args.arm:14s}: {'RESOLVED' if resolved else 'no':>8s} [{status}] "
                  f"tokens={run.usage.total:,} tools={len(run.tool_calls)} "
                  f"edited={run.files_edited} stop={run.stop_reason}")

            if args.persist_on_success and resolved and getattr(run, "htn", None):
                from app.services.method_library import persist_plan
                await persist_plan(
                    pool, embedder, sample["problem_statement"], run.htn["plan"],
                    steps_used=run.steps, touch_tags=touch_tags_from_run(run))
                print("  persisted this plan to the method library")

            shutil.rmtree(work, ignore_errors=True)
        finally:
            remove_image(image_for(sample))
            shutil.rmtree(inst_dir, ignore_errors=True)
        return 0
    finally:
        await restore_all(pool)
        Path(args.out).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
