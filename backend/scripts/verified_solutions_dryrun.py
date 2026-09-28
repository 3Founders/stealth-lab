"""
Read-only dry run for the verified-solution corpora.

Applies every admission gate to real upstream rows and prints the metrics
table, WITHOUT writing to any database, WITHOUT calling an LLM, and
therefore at zero spend. It exists because the interesting numbers in this
step -- how many rows survive the license gate, how many are held out, how
many dedupe -- are knowable before a single row is ingested, and knowing
them first is what makes the production run a decision rather than a hope.

Read-only by construction: it imports the reader and the license policy and
nothing else from the ingestion stack. It cannot reach a pool, a shard, or
a model.

    python scripts/verified_solutions_dryrun.py --source swe_bench_extra --target 500
    python scripts/verified_solutions_dryrun.py --all --target 500
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.ingestion_sources import verified_solutions_hf as vs  # noqa: E402
from app.services.repo_license_policy import classify_spdx  # noqa: E402

DESIGN_PATHS = (
    REPO_ROOT / "experiments" / "swebench" / "runs" / "design.json",
    REPO_ROOT / "experiments" / "swebench_rebench" / "runs" / "design.json",
)


def scan(source_key: str, target: int, max_scan: int, include_repos: bool) -> dict:
    held = vs.load_held_out(DESIGN_PATHS, include_repos=include_repos)
    counters = vs.GateCounters()
    languages: Counter[str] = Counter()
    seen_bytes = 0
    accepted = 0
    started = time.time()
    # max_scan caps rows READ (inside the source), so a corpus that admits almost nothing still stops;
    # counting in this loop only counted admitted rows and never fired for such a corpus.
    for row, content in vs.VerifiedSolutionSource(
        source_key,
        held_out=held,
        license_classify=classify_spdx,
        counters=counters,
        scan_limit=max_scan,
    ).iter_admissible():
        accepted += 1
        languages[row.language] += 1
        seen_bytes += len(content.encode("utf-8"))
        if accepted >= target:
            break
    return {
        "source": source_key,
        "dataset": vs.SOURCES[source_key]["dataset_repo"],
        "revision": vs.SOURCES[source_key]["revision"],
        "split": vs.SOURCES[source_key]["split"],
        "target": target,
        "accepted": accepted,
        "rows_seen": counters.rows_seen,
        "rejected": dict(sorted(counters.reasons.items(), key=lambda kv: -kv[1])),
        "duplicates": counters.duplicate,
        "licenses": dict(sorted(counters.license_counts.items(), key=lambda kv: -kv[1])),
        "languages": dict(languages.most_common()),
        "bytes_total": seen_bytes,
        "bytes_per_item": round(seen_bytes / accepted, 1) if accepted else 0,
        "wall_seconds": round(time.time() - started, 1),
        "wall_seconds_per_item": round((time.time() - started) / accepted, 3) if accepted else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append", choices=sorted(vs.SOURCES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--target", type=int, default=500)
    ap.add_argument("--max-scan", type=int, default=20000)
    ap.add_argument("--no-repo-exclusion", action="store_true")
    args = ap.parse_args()

    keys = sorted(vs.SOURCES) if args.all else (args.source or [])
    if not keys:
        ap.error("pass --source or --all")

    results = []
    for key in keys:
        print(f"scanning {key} ...", file=sys.stderr, flush=True)
        try:
            results.append(
                scan(key, args.target, args.max_scan, not args.no_repo_exclusion)
            )
        except Exception as exc:  # a corpus being unavailable is a result
            results.append({"source": key, "error": f"{type(exc).__name__}: {exc}"})

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
