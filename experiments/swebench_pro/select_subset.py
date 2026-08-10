"""
Pick the eval instances and freeze the split, before any model runs.

Selection is date-ordered, not random, because the memory arm is only
allowed to remember the past. Every eval instance's store is built from
ansible instances with a strictly earlier commit date, so nothing an agent
sees was written after the bug it is being asked to fix. Randomly splitting
would put later fixes in the memory of earlier issues, and any accuracy gain
from that is leakage rather than memory.

Eval instances are drawn from the *latest* instances by date so that each
has the deepest possible history behind it, and the split is written to
subset.json so the same instances are used by both arms and by anyone
re-running this.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess

from datasets import load_dataset

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "ansible/ansible"


def commit_dates(git_dir: str, shas: list[str]) -> dict[str, str]:
    """One `git show` per commit against a local blobless clone. Cheap, and
    unlike the GitHub API it isn't rate-limited at 60/hour."""
    out: dict[str, str] = {}
    for sha in shas:
        proc = subprocess.run(
            ["git", "-C", git_dir, "show", "-s", "--format=%cI", sha],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            out[sha] = proc.stdout.strip()
    return out


def files_touched(patch: str) -> list[str]:
    return sorted(set(re.findall(r"^diff --git a/(\S+) b/", patch, re.MULTILINE)))


def symbols_touched(patch: str) -> list[str]:
    """Names off the hunk headers plus definitions added in the diff. This is
    the localization signal a repo-experienced engineer carries around, and
    it comes free from the diff -- no LLM call at ingestion."""
    syms = set(re.findall(r"^@@ .*@@\s*(?:def|class)\s+(\w+)", patch, re.MULTILINE))
    syms |= set(re.findall(r"^\+\s*(?:def|class)\s+(\w+)", patch, re.MULTILINE))
    return sorted(syms)


def title_of(problem_statement: str) -> str:
    """Pro problem statements open with a bolded title line."""
    m = re.search(r"\*\*Title:\s*(.+?)\*\*", problem_statement)
    if m:
        return m.group(1).strip()
    first = problem_statement.strip().split("\n")[0]
    return re.sub(r"^[#*\s\"]+|[\"*\s]+$", "", first)[:160]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--git-dir", required=True, help="local ansible clone")
    ap.add_argument("--n-eval", type=int, default=20)
    ap.add_argument("--scripts-dir", required=True)
    args = ap.parse_args()

    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    rows = [dict(r) for r in ds if r["repo"] == REPO]
    print(f"{REPO}: {len(rows)} instances")

    # Drop anything the OS harness has no run_script for -- it cannot be
    # graded, so it cannot be evaluated, so it should not be selected.
    rows = [r for r in rows
            if os.path.isdir(os.path.join(args.scripts_dir, r["instance_id"]))]
    print(f"with run_scripts: {len(rows)}")

    dates = commit_dates(args.git_dir, [r["base_commit"] for r in rows])
    rows = [r for r in rows if r["base_commit"] in dates]
    for r in rows:
        r["commit_date"] = dates[r["base_commit"]]
        r["files_touched"] = files_touched(r["patch"])
        r["symbols_touched"] = symbols_touched(r["patch"])
        r["title"] = title_of(r["problem_statement"])
    rows.sort(key=lambda r: r["commit_date"])
    print(f"dated: {len(rows)}  span {rows[0]['commit_date'][:10]} .. {rows[-1]['commit_date'][:10]}")

    # Latest N as eval; everything strictly earlier is available as memory.
    eval_rows = rows[-args.n_eval:]
    corpus_rows = rows[: -args.n_eval]

    def slim(r: dict, keep_patch: bool) -> dict:
        out = {
            "instance_id": r["instance_id"],
            "repo": r["repo"],
            "base_commit": r["base_commit"],
            "commit_date": r["commit_date"],
            "title": r["title"],
            "problem_statement": r["problem_statement"],
            "files_touched": r["files_touched"],
            "symbols_touched": r["symbols_touched"],
            "dockerhub_tag": r["dockerhub_tag"],
            "before_repo_set_cmd": r["before_repo_set_cmd"],
            "selected_test_files_to_run": r["selected_test_files_to_run"],
            "fail_to_pass": r["fail_to_pass"],
            "pass_to_pass": r["pass_to_pass"],
        }
        if keep_patch:
            # Gold stays only on eval rows, and only so the harness can be
            # validated against it. It is never handed to an agent.
            out["patch"] = r["patch"]
            out["requirements"] = r.get("requirements", "")
            out["interface"] = r.get("interface", "")
        return out

    subset = {
        "repo": REPO,
        "model_note": "eval = latest N by commit date; memory = strictly earlier only",
        "eval": [slim(r, True) for r in eval_rows],
        "corpus": [slim(r, False) for r in corpus_rows],
    }
    path = os.path.join(HERE, "subset.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subset, f, indent=1)

    print(f"\neval   : {len(eval_rows)}  {eval_rows[0]['commit_date'][:10]} .. {eval_rows[-1]['commit_date'][:10]}")
    print(f"corpus : {len(corpus_rows)}  {corpus_rows[0]['commit_date'][:10]} .. {corpus_rows[-1]['commit_date'][:10]}")
    print(f"wrote {path}")

    # How much of the eval set is even reachable from memory? If prior issues
    # never touch the same files, there is nothing for retrieval to transfer
    # and the experiment has no headroom -- better to know that now.
    corpus_files = {f for r in corpus_rows for f in r["files_touched"]}
    overlap = [len(set(r["files_touched"]) & corpus_files) / max(1, len(r["files_touched"]))
               for r in eval_rows]
    print(f"\neval files previously touched in corpus: "
          f"mean {sum(overlap) / len(overlap):.0%}, "
          f"{sum(1 for o in overlap if o > 0)}/{len(overlap)} instances with any overlap")


if __name__ == "__main__":
    main()
