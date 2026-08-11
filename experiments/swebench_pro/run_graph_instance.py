"""
One instance, end to end, against the real graph.

  hold out the instance (bi-temporal invalidation)
  rebuild the HTN tree over what remains
  retrieve with HybridRetriever + hierarchical_search
  score the retrieval against the gold patch's actual files
  run the agent twice -- no memory, and graph memory
  grade both against the real test suite
  restore

The gold run comes FIRST and is not optional. An instance whose reference
patch does not resolve is a row both arms are guaranteed to lose, and
finding that out costs seconds instead of two agent runs. NodeBB in the
pilot is exactly this case.

RETRIEVAL IS SCORED SEPARATELY FROM RESOLUTION, and that separation is the
point of running this at all. There are two independent ways the memory arm
can fail: retrieval can return irrelevant precedents, or retrieval can be
right and the agent can fail to act on it. The previous 9-instance run
could not tell those apart -- it reported only resolved/not. Here,
`file_recall` says whether the graph found where the fix belongs, and
`resolved` says whether that helped. A run with high recall and no
resolution gain is a scaffold problem; low recall is a retrieval problem.
They need opposite fixes.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE.parents[1]))

from app.db.session import create_pool  # noqa: E402
from experiments.after.embed_cache import CachedEmbedder  # noqa: E402
from agent import Agent, RepoSandbox  # noqa: E402
from graph_ingest import (  # noqa: E402
    SWEBENCH_DSN, load_dataset, normalize_statement, patch_facts, title_of,
)
from graph_memory import (  # noqa: E402
    hold_out, htn_route, rebuild_hierarchy, render_context, restore_all, retrieve,
)
from pro_harness import evaluate, image_for, pull_image, remove_image  # noqa: E402
from run_experiment import extract, snapshot_repo  # noqa: E402

DEFAULT_SCRIPTS = os.path.expanduser("~/AppData/Local/Temp/swebench_pro_os/run_scripts")


# Files no agent can produce by hand. A gold patch that regenerates
# protobuf stubs or re-resolves a lockfile is not a reasoning task -- it is
# a `make generate` task, and an agent will fail it regardless of how good
# retrieval was. Selecting past those is not cherry-picking a winnable
# instance; it is excluding one where the outcome carries no information
# about the thing being measured. The count of what was skipped, and why,
# goes into the result record.
_GENERATED = (".pb.go", ".pb.gw.go", "_grpc.pb.go", "_generated.go", ".gen.go",
              "go.sum", "go.mod", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
              ".snap", ".pot", ".min.js")


def _hand_editable(files: list[str]) -> list[str]:
    return [f for f in files if not any(f.endswith(s) for s in _GENERATED)]


def pick_instance(df, instance_id: str | None) -> tuple:
    if instance_id:
        rows = df[df["instance_id"] == instance_id]
        if rows.empty:
            raise SystemExit(f"no such instance: {instance_id}")
        return rows.iloc[0], {"selected_by": "explicit"}

    # Default: a repo whose gold patch the pilot verified, preferring one
    # that is NOT ansible -- ansible is the only repo the agent has ever been
    # measured on, and also the only one where the old search allowlist was
    # not blind, so a Go or TS instance exercises the part that was broken.
    pilot = HERE / "pilot_gold_results.json"
    verified = set()
    if pilot.exists():
        verified = {r["repo"] for r in json.loads(pilot.read_text())["results"]
                    if r["resolved"]}
    preferred = [r for r in ("flipt-io/flipt", "navidrome/navidrome",
                             "gravitational/teleport", "qutebrowser/qutebrowser",
                             "ansible/ansible") if not verified or r in verified]

    best, skipped = None, 0
    for repo in preferred:
        for _, row in df[df["repo"] == repo].iterrows():
            files, _ = patch_facts(str(row["patch"]))
            editable = _hand_editable(files)
            if not editable or len(editable) != len(files):
                skipped += 1
                continue
            if 1 <= len(editable) <= 3 and (best is None or len(editable) < best[1]):
                best = (row, len(editable))
        if best:
            return best[0], {"selected_by": "fewest hand-editable gold files",
                             "repo_pool": repo, "gold_file_count": best[1],
                             "skipped_generated_only": skipped}
    return df.iloc[0], {"selected_by": "fallback", "skipped_generated_only": skipped}


def _dirs(files) -> set[str]:
    return {f.rsplit("/", 1)[0] for f in files if "/" in f}


def _added_lines(patch: str) -> set[str]:
    """Substantive added lines from a diff: '+' lines, minus the '+++' file
    headers, minus whitespace-only and single-token noise (a lone brace or
    'import' matches everywhere and would inflate any overlap measure)."""
    out = set()
    for line in (patch or "").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].strip()
        if len(body) < 8 or body in {"}", "{", ")", "});", "import (", "*/"}:
            continue
        out.add(body)
    return out


def score_copyability(hits, gold_patch: str) -> dict:
    """
    How much of the gold patch was ALREADY IN what the agent was shown?

    This is the number that makes a patches-included result interpretable.
    Resolution alone cannot separate "reasoned out the fix" from "adapted a
    near-duplicate that was handed over", and with diffs in the context the
    second is a live possibility rather than a hypothetical. High copyability
    plus a resolve means the run measured near-duplicate lookup; low
    copyability plus a resolve means the precedents helped without containing
    the answer. Both are real results -- they are just different ones, and
    the distinction is invisible without this.

    Exact-line overlap is deliberately strict. It answers "could this have
    been copied verbatim", not "is this thematically similar", because
    verbatim availability is what would make the accuracy number hollow.
    """
    gold_added = _added_lines(gold_patch)
    if not gold_added:
        return {"gold_added_lines": 0, "max_copyable_fraction": None}

    per_hit = []
    union = set()
    for h in hits:
        shown = _added_lines(getattr(h, "patch", "") or "")
        common = gold_added & shown
        union |= common
        per_hit.append({
            "instance_id": h.instance_id,
            "shown_added_lines": len(shown),
            "overlap_with_gold": len(common),
            "fraction_of_gold": round(len(common) / len(gold_added), 3),
        })
    best = max((p["fraction_of_gold"] for p in per_hit), default=0.0)
    return {
        "gold_added_lines": len(gold_added),
        "max_copyable_fraction": best,
        "union_copyable_fraction": round(len(union) / len(gold_added), 3),
        "any_precedent_contains_gold_line": bool(union),
        "example_copyable_lines": sorted(union)[:5],
        "per_precedent": per_hit,
    }


def score_retrieval(hits, gold_files: list[str], gold_repo: str) -> dict:
    """
    Did the graph point at the right place?

    `file_recall` is the headline and it is exact-match. `dir_recall` and
    `prefix_depth` are diagnostics for WHY it reads zero, not softer
    restatements of it: exact-match cannot distinguish "retrieved something
    in a completely unrelated subsystem" from "retrieved the neighbouring
    file in the same package". On the flipt instance the gold file is
    internal/server/auth/middleware.go and a hit touched
    internal/server/middleware/grpc/middleware.go -- adjacent work, zero
    exact recall. Those two cases call for opposite responses, so both are
    recorded. The headline number does not move.
    """
    retrieved = sorted({f for h in hits for f in h.files})
    gold = set(gold_files)
    overlap = gold & set(retrieved)
    same_repo = sum(1 for h in hits if h.repo == gold_repo)

    gold_dirs, ret_dirs = _dirs(gold), _dirs(retrieved)
    dir_overlap = gold_dirs & ret_dirs

    # Deepest shared path prefix between any gold file and any retrieved
    # file, in path segments. 0 = nothing in common at all.
    best_prefix = 0
    for g in gold:
        gp = g.split("/")
        for r in retrieved:
            rp = r.split("/")
            n = 0
            for a, b in zip(gp[:-1], rp[:-1]):
                if a != b:
                    break
                n += 1
            best_prefix = max(best_prefix, n)

    return {
        "n_hits": len(hits),
        "retrieved_files": len(retrieved),
        "gold_files": len(gold),
        "files_hit": sorted(overlap),
        "file_recall": round(len(overlap) / len(gold), 3) if gold else None,
        "file_precision": round(len(overlap) / len(retrieved), 3) if retrieved else None,
        "dir_recall": round(len(dir_overlap) / len(gold_dirs), 3) if gold_dirs else None,
        "dirs_hit": sorted(dir_overlap),
        "deepest_shared_prefix_segments": best_prefix,
        "gold_dirs": sorted(gold_dirs),
        "same_repo_hits": same_repo,
        "same_repo_rate": round(same_repo / len(hits), 3) if hits else None,
        "hit_instances": [h.instance_id for h in hits],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", default=None)
    ap.add_argument("--dsn", default=SWEBENCH_DSN)
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS)
    ap.add_argument("--work-dir", default=os.path.expanduser("~/AppData/Local/Temp/swebench_graph"))
    ap.add_argument("--model", default="deepseek-v3.1")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--skip-agent", action="store_true",
                    help="retrieval only -- no LLM calls, no containers")
    ap.add_argument("--include-patches", action="store_true",
                    help="show each retrieved precedent's actual gold diff to "
                         "the agent. Turns the run into a near-duplicate-lookup "
                         "measurement; read `copyability` alongside `resolved`.")
    ap.add_argument("--include-requirements", action="store_true",
                    help="show what each precedent's fix had to satisfy")
    ap.add_argument("--patch-chars", type=int, default=1400,
                    help="per-precedent diff budget in the memory block")
    ap.add_argument("--memory-chars", type=int, default=6000,
                    help="total memory block budget; the block is resent on "
                         "every call, so this multiplies across the episode")
    ap.add_argument("--out", default=str(HERE / "graph_instance_result.json"))
    args = ap.parse_args()

    df = load_dataset()
    sample, selection = pick_instance(df, args.instance_id)
    iid = sample["instance_id"]
    gold_files, gold_symbols = patch_facts(str(sample["patch"]))
    title = title_of(sample["problem_statement"])
    print(f"instance : {iid}")
    print(f"repo     : {sample['repo']} ({sample['repo_language']})")
    print(f"title    : {title[:100]}")
    print(f"gold     : {len(gold_files)} files, {len(gold_symbols)} symbols")
    print(f"           {gold_files}")
    print(f"selection: {selection}")

    embedder = CachedEmbedder(min_interval=21.0)
    embedder.MAX_BATCH_TOKENS = 3300
    pool = await create_pool(dsn=args.dsn, min_size=1, max_size=4)
    record: dict = {"instance_id": iid, "repo": sample["repo"],
                    "language": sample["repo_language"], "title": title,
                    "gold_files": gold_files, "model": args.model,
                    "max_steps": args.max_steps, "selection": selection}

    try:
        await restore_all(pool)
        n = await hold_out(pool, iid)
        live = await pool.fetchval(
            "SELECT count(*) FROM task_nodes WHERE created_by='swebench_ingest' "
            "AND t_invalid IS NULL")
        print(f"\nheld out {n} rows; {live} task nodes remain live")
        record["held_out_rows"], record["live_task_nodes"] = n, live

        print("rebuilding HTN tree over the remaining leaves...")
        t0 = time.time()
        reports = await rebuild_hierarchy(pool, embedder)
        print(f"  built in {time.time()-t0:.1f}s: {reports}")
        record["hierarchy"] = {k: str(v) for k, v in reports.items()}

        query = f"{title}\n\n{normalize_statement(sample['problem_statement'])[:1500]}"
        t0 = time.time()
        hits, diag = await retrieve(pool, query, embedder, top_k=args.top_k)
        route = await htn_route(pool, query, embedder)
        print(f"\nretrieval ({time.time()-t0:.1f}s): {diag}")
        print(f"HTN descent: {route}")

        # The held-out instance must NOT come back. If it does, the
        # bi-temporal filter leaked and every number below is meaningless.
        if any(h.instance_id == iid for h in hits) or route.get("instance_id") == iid:
            print("FATAL: the held-out instance retrieved ITSELF -- t_invalid "
                  "is not being honoured somewhere. Aborting; any result from "
                  "this state would be measuring leakage.")
            record["error"] = "holdout_leaked"
            Path(args.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
            return 1

        rscore = score_retrieval(hits, gold_files, sample["repo"])
        record["retrieval"], record["htn"], record["retrieval_diag"] = rscore, route, diag
        print(f"\nretrieval quality: file_recall={rscore['file_recall']} "
              f"precision={rscore['file_precision']} "
              f"same_repo={rscore['same_repo_rate']} hit={rscore['files_hit']}")
        print(f"  diagnostics: dir_recall={rscore['dir_recall']} "
              f"dirs_hit={rscore['dirs_hit']} "
              f"deepest_shared_prefix={rscore['deepest_shared_prefix_segments']} "
              f"gold_dirs={rscore['gold_dirs']}")
        for h in hits:
            print(f"  [{h.score:.4f} {'+'.join(h.matched_by):<16}] [{h.repo}] {h.title[:64]}")

        memory_block = render_context(
            hits, max_chars=args.memory_chars,
            include_patches=args.include_patches,
            patch_chars=args.patch_chars,
            include_requirements=args.include_requirements,
        )
        record["memory_block_chars"] = len(memory_block)
        record["arm_config"] = {
            "include_patches": args.include_patches,
            "include_requirements": args.include_requirements,
            "patch_chars": args.patch_chars,
            "memory_chars": args.memory_chars,
        }
        record["copyability"] = score_copyability(hits, str(sample["patch"]))
        c = record["copyability"]
        print(f"\ncopyability: max_copyable_fraction={c.get('max_copyable_fraction')} "
              f"union={c.get('union_copyable_fraction')} "
              f"of {c.get('gold_added_lines')} gold added lines")
        if c.get("example_copyable_lines"):
            print(f"  gold lines already present in the shown precedents:")
            for ln in c["example_copyable_lines"]:
                print(f"    {ln[:100]}")
        print(f"\nmemory block: {len(memory_block)} chars")
        print("-" * 70)
        print(memory_block[:1200])
        print("-" * 70)

        if args.skip_agent:
            Path(args.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
            print(f"\n--skip-agent: wrote {args.out}")
            return 0

        from openai import OpenAI

        from app.config import settings

        # settings, not os.environ: the key lives in backend/.env and is not
        # exported into the process environment. os.environ would KeyError
        # here -- after the image pull and the gold run, i.e. after the
        # expensive part. Same class of failure as the .env-resolution bug.
    # max_retries=0: the SDK's OWN retry loop must be OFF.
    # Diagnosed from a live stack dump of a run that produced 0 rows in 53
    # minutes -- it sat in openai/_base_client.py:_sleep_for_retry. `timeout`
    # bounds ONE http attempt; the SDK then sleeps and retries OUTSIDE that
    # timeout, and only after exhausting its own retries does our handler see
    # an exception and start OUR retry loop. The two nest multiplicatively.
    # One bounded retry policy, in Agent._chat, where it can be reasoned about.
        client = OpenAI(max_retries=0, api_key=settings.require("general_compute_api_key"),
                        base_url=settings.general_compute_base_url
                        or "https://api.generalcompute.com/v1")
        agent = Agent(client, args.model, max_steps=args.max_steps)

        inst_dir = os.path.join(args.work_dir, iid)
        pull_image(image_for(sample))
        try:
            tar_path = snapshot_repo(sample, os.path.join(inst_dir, "snap"))
            gold = evaluate(sample, str(sample["patch"]), args.scripts_dir,
                            os.path.join(inst_dir, "gold_ws"), keep_image=True)
            record["gold"] = {"resolved": gold.resolved, "status": gold.status,
                              "n_tests_parsed": gold.n_tests_parsed}
            print(f"\ngold: {'RESOLVED' if gold.resolved else 'FAILED - instance unusable'} "
                  f"[{gold.status}]")
            if not gold.resolved:
                record["excluded"] = "gold_patch_does_not_resolve"
                return 1

            for arm, block in (("no_memory", ""), ("graph_memory", memory_block)):
                work = extract(tar_path, os.path.join(inst_dir, f"repo_{arm}"))
                run = agent.run(sample, RepoSandbox(work), arm, memory_block=block)
                if run.patch.strip():
                    res = evaluate(sample, run.patch, args.scripts_dir,
                                   os.path.join(inst_dir, f"{arm}_ws"), keep_image=True)
                    graded = {"f2p_passed": len(res.f2p_passed),
                              "f2p_missing": len(res.f2p_missing),
                              "p2p_broke": len(res.p2p_broke),
                              "apply_status": res.apply_status}
                    resolved, status = res.resolved, res.status
                else:
                    resolved, status, graded = False, "no_patch", {}
                # An episode the provider killed is NOT a task failure. It
                # graded as f2p_failed only because the agent was cut off
                # mid-run -- 34 of 40 steps used, one edit made, `finish`
                # never reached. Recording that as a legitimate loss is the
                # exact bug that invalidated Experiment 1 Hypothesis B, where
                # provider "Blocked" pages were stored as feasible=False and
                # were indistinguishable from genuine infeasibility. Flag it
                # so the row can be excluded rather than averaged in.
                invalid = run.stop_reason == "api_error"
                record[arm] = {
                    "valid": not invalid,
                    "invalid_reason": "provider_error_truncated_episode" if invalid else None,
                    "resolved": resolved, "status": status,
                    "total_tokens": run.usage.total, "llm_calls": run.usage.calls,
                    "n_tool_calls": len(run.tool_calls), "tool_calls": run.tool_calls,
                    "files_edited": run.files_edited,
                    "files_edited_correct": sorted(set(run.files_edited) & set(gold_files)),
                    "stop_reason": run.stop_reason, "patch_bytes": len(run.patch),
                    "wall_seconds": round(run.wall_seconds, 1),
                    "agent_error": run.error, "graded": graded,
                }
                print(f"{arm:14s}: {'RESOLVED' if resolved else 'no':>8s} [{status}] "
                      f"tokens={run.usage.total:,} tools={len(run.tool_calls)} "
                      f"edited={run.files_edited} stop={run.stop_reason}"
                      f"{'  *** INVALID: provider killed the episode ***' if invalid else ''}")
                shutil.rmtree(work, ignore_errors=True)
        finally:
            remove_image(image_for(sample))
            shutil.rmtree(inst_dir, ignore_errors=True)
        return 0
    finally:
        await restore_all(pool)
        Path(args.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
