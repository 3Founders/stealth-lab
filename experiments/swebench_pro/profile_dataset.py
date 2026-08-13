"""
Profile ScaleAI/SWE-bench_Pro to pick a runnable subset.

The binding constraint on this machine is disk: 36 GB free, and every Pro
instance has its own multi-GB image. So the selection is not "pick the
interesting instances", it is "pick a repo whose images share layers and
whose suite finishes in minutes", then take instances within it.

Writes profile.json; prints a per-repo table.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

import requests
from datasets import load_dataset

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.environ.get("PRO_SCRIPTS_DIR", "")


def dockerhub_size(tag: str) -> float | None:
    """Compressed size in GB, or None if the tag isn't published."""
    url = f"https://hub.docker.com/v2/repositories/jefzda/sweap-images/tags/{tag}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return round(r.json()["full_size"] / 1e9, 2)
    except Exception:
        return None


def run_script_needs_network(instance_id: str) -> bool | None:
    """
    Whether the repo's own test setup reaches the network.

    Matters because the whole security argument in repo_execution.py is that
    nothing executing a candidate patch has network access. A run script that
    does `npm install` or `go mod download` at test time cannot honour that,
    and knowing which repos those are is a selection criterion, not a detail.
    """
    if not SCRIPTS_DIR:
        return None
    path = os.path.join(SCRIPTS_DIR, instance_id, "run_script.sh")
    if not os.path.exists(path):
        return None
    body = open(path, encoding="utf-8", errors="replace").read()
    net = r"(npm|yarn|pnpm) (install|ci)\b|go mod (download|tidy)|pip install|apt-get|curl |wget "
    return bool(re.search(net, body))


def main() -> None:
    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    print(f"loaded {len(ds)} instances", file=sys.stderr)

    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in ds:
        by_repo[row["repo"]].append(row)

    profile = {}
    for repo, rows in sorted(by_repo.items(), key=lambda kv: -len(kv[1])):
        langs = sorted({r["repo_language"] for r in rows})
        # Sample one image per repo -- instances of the same repo are built
        # from a shared base, so one is representative for sizing.
        probe = rows[0]
        size = dockerhub_size(probe["dockerhub_tag"])
        net = run_script_needs_network(probe["instance_id"])

        # Test-suite breadth is the other cost driver: a P2P list in the
        # thousands means every run re-executes the whole suite.
        p2p_sizes = [len(json.loads(_norm(r["pass_to_pass"]))) for r in rows]
        f2p_sizes = [len(json.loads(_norm(r["fail_to_pass"]))) for r in rows]

        profile[repo] = {
            "n": len(rows),
            "languages": langs,
            "image_gb_compressed": size,
            "run_script_needs_network": net,
            "median_p2p": sorted(p2p_sizes)[len(p2p_sizes) // 2],
            "median_f2p": sorted(f2p_sizes)[len(f2p_sizes) // 2],
            "median_patch_bytes": sorted(len(r["patch"]) for r in rows)[len(rows) // 2],
            "n_test_files_median": sorted(
                len(json.loads(_norm(r["selected_test_files_to_run"]))) for r in rows
            )[len(rows) // 2],
        }
        print(
            f"{repo:35s} n={len(rows):4d} {','.join(langs):12s} "
            f"img={size}GB net={net} p2p~{profile[repo]['median_p2p']:5d} "
            f"f2p~{profile[repo]['median_f2p']:3d} files~{profile[repo]['n_test_files_median']}"
        )

    with open(os.path.join(HERE, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    print(f"\nwrote {os.path.join(HERE, 'profile.json')}", file=sys.stderr)


def _norm(value: str) -> str:
    """Pro stores these as Python-repr lists (single quotes), not JSON."""
    if value.strip().startswith("["):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps(eval(value))  # noqa: S307 - dataset-controlled literal
    return "[]"


if __name__ == "__main__":
    main()
