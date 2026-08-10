"""
Orchestrator: for each eval instance, run both arms and grade both against
the real test suite.

ORDER OF OPERATIONS PER INSTANCE, and why it is this order:

  pull image
  snapshot /app at base_commit          <- network on, nothing executed
  gold run                              <- proves the instance is gradeable
  arm A (no memory)   -> patch -> grade <- network off inside the container
  arm B (with memory) -> patch -> grade
  rmi image

The gold run comes first and is not optional. An instance where the gold
patch fails to resolve is a broken instance, and letting one into the eval
set adds a row both arms are guaranteed to fail -- which drags accuracy
toward zero and hides a real difference. Better to find out for 14 seconds
of compute than after both agent runs.

Both arms start from a fresh extraction of the same tar, so arm B never sees
a file arm A touched. Arm order is fixed rather than randomized: the arms
share no state, and a fixed order keeps reruns comparable.

The image is removed after each instance because Pro images share no layers
(measured: two ansible instances, 12 and 15 layers, empty intersection) and
this machine has 36 GB free against ~1 GB per instance.

Resumable: results.jsonl is append-only and completed instances are skipped,
so an interrupted run continues rather than restarting and re-billing.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent, RepoSandbox  # noqa: E402
from knowledge import VoyageEmbedder, build_store, render_context  # noqa: E402
from pro_harness import (  # noqa: E402
    HarnessError, evaluate, image_for, pull_image, remove_image,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def snapshot_repo(sample: dict, out_dir: str) -> str:
    """
    Materialize the repository as the tests will see it, minus the tests.

    `git reset --hard base_commit` only -- deliberately NOT
    before_repo_set_cmd, which checks out the new test files from the
    solution commit. Those files are the F2P tests. An agent that could read
    them would be reading the answer key, so the snapshot stops one step
    earlier than the grading container does.
    """
    os.makedirs(out_dir, exist_ok=True)
    tar_path = os.path.join(out_dir, "repo.tar")
    if not os.path.exists(tar_path):
        mount = os.path.abspath(out_dir).replace("\\", "/")
        script = (
            f"cd /app && git reset --hard {sample['base_commit']} -q && "
            f"git checkout {sample['base_commit']} -q && "
            "tar -cf /out/repo.tar --exclude=./.git ."
        )
        proc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{mount}:/out",
             "--entrypoint", "/bin/bash", image_for(sample), "-c", script],
            capture_output=True, text=True, timeout=900, errors="replace",
        )
        if proc.returncode != 0 or not os.path.exists(tar_path):
            raise HarnessError(f"snapshot failed: {proc.stderr.strip()[:400]}")
    return tar_path


def extract(tar_path: str, dest: str) -> str:
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(tar_path) as tf:
        # filter="data" refuses absolute paths and traversal entries. The tar
        # is ours, but a tarfile extraction without it is the classic way an
        # archive writes outside its destination.
        tf.extractall(dest, filter="data")
    return dest


def load_done(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["instance_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-dir", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--model", default="gemma-4-31B-it")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="0 = all eval instances")
    ap.add_argument("--out", default=os.path.join(HERE, "results.jsonl"))
    args = ap.parse_args()

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["GENERAL_COMPUTE_API_KEY"],
                    base_url="https://api.generalcompute.com/v1")
    agent = Agent(client, args.model, max_steps=args.max_steps)

    with open(os.path.join(HERE, "subset.json"), encoding="utf-8") as f:
        subset = json.load(f)
    eval_rows = subset["eval"]
    if args.limit:
        eval_rows = eval_rows[: args.limit]

    embedder = VoyageEmbedder(os.path.join(args.work_dir, "voyage_cache.json"))
    done = load_done(args.out)
    print(f"{len(eval_rows)} eval instances, {len(done)} already done\n")

    for i, sample in enumerate(eval_rows, 1):
        iid = sample["instance_id"]
        if iid in done:
            print(f"[{i}/{len(eval_rows)}] skip (done) {iid[:64]}")
            continue

        print(f"[{i}/{len(eval_rows)}] {iid[:64]}\n    {sample['title'][:100]}")
        inst_dir = os.path.join(args.work_dir, iid)
        record: dict = {"instance_id": iid, "title": sample["title"],
                        "commit_date": sample["commit_date"],
                        "files_touched": sample["files_touched"],
                        "model": args.model, "max_steps": args.max_steps}
        t0 = time.time()

        try:
            pull_image(image_for(sample))
            tar_path = snapshot_repo(sample, os.path.join(inst_dir, "snap"))

            gold = evaluate(sample, sample["patch"], args.scripts_dir,
                            os.path.join(inst_dir, "gold_ws"), keep_image=True)
            record["gold"] = {"resolved": gold.resolved, "status": gold.status,
                              "n_tests_parsed": gold.n_tests_parsed}
            print(f"    gold: {'RESOLVED' if gold.resolved else 'FAILED — instance unusable'} "
                  f"[{gold.status}]")
            if not gold.resolved:
                record["excluded"] = "gold_patch_does_not_resolve"
                _append(args.out, record)
                continue

            # Memory is rebuilt per instance: the cutoff is that instance's
            # own commit date, so the store legitimately differs row to row.
            store = build_store(subset["corpus"], sample["commit_date"], embedder)
            store.build_embeddings()
            hits = store.retrieve(
                f"{sample['title']}\n\n{sample['problem_statement'][:1500]}",
                top_k=args.top_k)
            memory_block = render_context(hits)
            # NOT "memory": the arm loop below writes record["memory"], and
            # an earlier version of this file used the same key for both --
            # the retrieval metadata was silently overwritten by the arm
            # result on every instance.
            record["memory_meta"] = {
                "corpus_size": len(store.nodes),
                "retrieved": [h.instance_id for h in hits],
                "retrieved_files": sorted({f for h in hits for f in h.files_touched}),
                "block_chars": len(memory_block),
            }
            overlap = set(record["memory_meta"]["retrieved_files"]) & set(sample["files_touched"])
            record["memory_meta"]["hit_files_correct"] = sorted(overlap)
            print(f"    memory: {len(store.nodes)} prior issues, top-{args.top_k} "
                  f"-> {len(record['memory_meta']['retrieved_files'])} candidate files, "
                  f"{len(overlap)}/{len(sample['files_touched'])} of the real ones")

            for arm, block, retrieved in (
                ("no_memory", "", []),
                ("memory", memory_block, [h.instance_id for h in hits]),
            ):
                work = extract(tar_path, os.path.join(inst_dir, f"repo_{arm}"))
                sandbox = RepoSandbox(work)
                run = agent.run(sample, sandbox, arm, memory_block=block,
                                retrieved=retrieved)
                if run.patch.strip():
                    res = evaluate(sample, run.patch, args.scripts_dir,
                                   os.path.join(inst_dir, f"{arm}_ws"), keep_image=True)
                    resolved, status = res.resolved, res.status
                    graded = {"f2p_passed": len(res.f2p_passed),
                              "f2p_missing": len(res.f2p_missing),
                              "p2p_broke": len(res.p2p_broke),
                              "apply_status": res.apply_status}
                else:
                    # No edit at all is a real outcome, not an error, and it
                    # is not worth 14s of container to confirm it fails.
                    resolved, status, graded = False, "no_patch", {}

                record[arm] = {
                    "resolved": resolved, "status": status,
                    "prompt_tokens": run.usage.prompt_tokens,
                    "completion_tokens": run.usage.completion_tokens,
                    "total_tokens": run.usage.total,
                    "llm_calls": run.usage.calls,
                    "tool_calls": run.tool_calls,
                    "n_tool_calls": len(run.tool_calls),
                    "files_edited": run.files_edited,
                    "patch_bytes": len(run.patch),
                    "stop_reason": run.stop_reason,
                    "wall_seconds": round(run.wall_seconds, 1),
                    "agent_error": run.error,
                    "graded": graded,
                }
                print(f"    {arm:10s}: {'RESOLVED' if resolved else 'no':>8s} "
                      f"[{status}] tokens={run.usage.total:,} "
                      f"tools={len(run.tool_calls)} stop={run.stop_reason}")
                shutil.rmtree(work, ignore_errors=True)

        except HarnessError as exc:
            record["error"] = str(exc)
            print(f"    harness error: {exc}")
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"    error: {type(exc).__name__}: {exc}")
        finally:
            record["wall_seconds"] = round(time.time() - t0, 1)
            _append(args.out, record)
            remove_image(image_for(sample))
            shutil.rmtree(inst_dir, ignore_errors=True)
            print(f"    ({record['wall_seconds']:.0f}s)\n")

    print(f"done -> {args.out}")


def _append(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
