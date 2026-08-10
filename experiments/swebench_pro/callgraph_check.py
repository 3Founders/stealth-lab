"""
Does static call-graph reachability (call_graph.py) have anything useful in
it? Answered against real data, before trusting the mechanism inside an
agent loop or spending any LLM budget on it -- same role retrieval_check.py
plays for the retrieval mechanism.

Targets Pattern A directly (GRAPH_EXPERIMENT.md section 8): the largest
unfixed SWE-bench Pro failure category is an agent editing some but not all
files a fix needs, because the missing one is reachable only by tracing a
call the issue text never mentions. For each checked instance:

  seed      = every top-level symbol in the FIRST gold-changed file
  reachable = call_graph.reachable_symbols(seed, ...), at 1/2/3 hops
  hit       = did any OTHER gold-changed file land in the reachable set?

Repos default to the five GRAPH_EXPERIMENT.md names as Pattern A examples.
Only instances with >=2 gold files are eligible -- a single-file fix has no
"other file" for this question to be about.

Uses Docker (the same pull_image/snapshot_repo/extract/remove_image this
experiment already uses to get a real checkout at the right commit) -- but
there is no LLM call and no Postgres connection anywhere in this script.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from call_graph import build_repo_symbol_index, reachable_symbols, seeds_in_file  # noqa: E402
from graph_ingest import load_dataset, patch_facts, title_of  # noqa: E402
from pro_harness import image_for, pull_image, remove_image  # noqa: E402
from run_experiment import extract, snapshot_repo  # noqa: E402

PATTERN_A_REPOS = (
    "internetarchive/openlibrary", "flipt-io/flipt", "gravitational/teleport",
    "navidrome/navidrome", "tutao/tutanota",
)
INSTANCES_PER_REPO = 2
HOPS_TO_TRY = (1, 2, 3)   # report the hop depth actually needed, not just pass/fail at one


def pick_instances(df, repos, per_repo: int) -> list:
    picked = []
    for repo in repos:
        count = 0
        for _, row in df[df["repo"] == repo].iterrows():
            files, _symbols = patch_facts(str(row["patch"]))
            if len(files) < 2:
                continue
            picked.append(row)
            count += 1
            if count >= per_repo:
                break
    return picked


def check_instance(sample, work_dir: str) -> dict:
    iid = sample["instance_id"]
    gold_files, _symbols = patch_facts(str(sample["patch"]))
    seed_file, target_files = gold_files[0], gold_files[1:]
    record: dict = {
        "instance_id": iid, "repo": sample["repo"], "gold_files": gold_files,
        "seed_file": seed_file, "target_files": target_files,
    }

    inst_dir = os.path.join(work_dir, iid)
    pull_image(image_for(sample))
    try:
        tar_path = snapshot_repo(sample, os.path.join(inst_dir, "snap"))
        root = extract(tar_path, os.path.join(inst_dir, "repo"))

        seeds = seeds_in_file(root, seed_file)
        record["seed_symbols"] = len(seeds)
        if not seeds:
            record["skipped"] = "no symbols found in seed file"
            return record

        t0 = time.time()
        index = build_repo_symbol_index(root)
        record["index_seconds"] = round(time.time() - t0, 1)
        record["index_files"] = index.files_indexed
        record["index_truncated"] = index.truncated

        record["hops"] = {}
        for hops in HOPS_TO_TRY:
            reach = reachable_symbols(seeds, root, index, max_hops=hops)
            hit_files = sorted(set(target_files) & set(reach.files))
            record["hops"][str(hops)] = {
                "reachable_files": len(reach.files),
                "hit_files": hit_files,
                "recall": round(len(hit_files) / len(target_files), 3) if target_files else None,
            }
    finally:
        remove_image(image_for(sample))
        shutil.rmtree(inst_dir, ignore_errors=True)
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", default=os.path.expanduser(
        "~/AppData/Local/Temp/swebench_callgraph_check"))
    ap.add_argument("--repos", default=",".join(PATTERN_A_REPOS))
    ap.add_argument("--per-repo", type=int, default=INSTANCES_PER_REPO)
    ap.add_argument("--out", default=str(HERE / "callgraph_check.json"))
    args = ap.parse_args()
    repos = [r.strip() for r in args.repos.split(",") if r.strip()]

    df = load_dataset()
    picked = pick_instances(df, repos, args.per_repo)
    print(f"{len(picked)} instances selected across {len(repos)} repos "
          f"-- this pulls one Docker image per instance\n")

    records = []
    for i, sample in enumerate(picked, 1):
        print(f"[{i}/{len(picked)}] {sample['repo']} - "
              f"{title_of(sample['problem_statement'])[:70]}")
        try:
            rec = check_instance(sample, args.work_dir)
        except Exception as exc:  # noqa: BLE001
            rec = {"instance_id": sample["instance_id"], "repo": sample["repo"],
                  "error": f"{type(exc).__name__}: {exc}"}
        records.append(rec)
        if rec.get("hops"):
            for hops, stats in rec["hops"].items():
                print(f"    hops={hops}: recall={stats['recall']} "
                      f"hit={stats['hit_files']} reachable_files={stats['reachable_files']}")
        elif rec.get("skipped"):
            print(f"    skipped: {rec['skipped']}")
        elif rec.get("error"):
            print(f"    error: {rec['error']}")

    print("\n" + "=" * 66)
    for hops in HOPS_TO_TRY:
        recalls = [r["hops"][str(hops)]["recall"] for r in records
                   if r.get("hops", {}).get(str(hops), {}).get("recall") is not None]
        if recalls:
            any_hit = sum(1 for r in recalls if r > 0)
            print(f"hops={hops}  mean recall={sum(recalls)/len(recalls):.3f}  "
                  f"any-hit={any_hit}/{len(recalls)}")
    n_skipped = sum(1 for r in records if r.get("skipped") or r.get("error"))
    print(f"instances={len(records)}  skipped_or_errored={n_skipped}")

    Path(args.out).write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
