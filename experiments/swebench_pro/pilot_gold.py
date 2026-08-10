"""
Pilot: does the harness grade the GOLD patch correctly in every repo?

The single acceptance test in validate_harness.py covered one instance of
ansible/ansible (Python). SWE-bench Pro spans 11 repos across 4 languages,
each with its own test runner and its own `parser.py`. A harness that
silently mis-parses Go or TypeScript output would report those instances
as unresolved no matter what patch it was given -- which looks exactly
like "the model failed" and would quietly bias every future comparison
against whichever repos are broken.

So: one (or more) instance per repo, run with the KNOWN-CORRECT patch.
Every one should resolve. Any that does not is a harness gap in that
repo's toolchain, identified before it can contaminate a result.

Images are pulled one at a time and deleted immediately after (pro_harness
does this in a `finally`), so peak disk stays at roughly one image (~1.3
GB) regardless of how many instances are swept. Bandwidth is the cost,
not storage.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pro_harness import HarnessError, evaluate, image_for  # noqa: E402

DEFAULT_SCRIPTS = os.path.expanduser("~/AppData/Local/Temp/swebench_pro_os/run_scripts")


def load_df():
    import pandas as pd

    p = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet"))[0]
    return pd.read_parquet(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-repo", type=int, default=1)
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS)
    ap.add_argument("--workspace", default=os.path.expanduser("~/AppData/Local/Temp/swebench_ws"))
    ap.add_argument("--timeout", type=int, default=2700)
    ap.add_argument("--out", default="pilot_gold_results.json")
    args = ap.parse_args()

    df = load_df()
    have = set(os.listdir(args.scripts_dir))
    picked = []
    for repo, grp in df.groupby("repo"):
        n = 0
        for _, row in grp.iterrows():
            if row["instance_id"] in have:
                picked.append({c: row[c] for c in df.columns})
                n += 1
            if n >= args.per_repo:
                break

    langs = {p["repo"]: p["repo_language"] for p in picked}
    print(f"pilot: {len(picked)} instances across {len(langs)} repos")
    for r, l in sorted(langs.items()):
        print(f"  {r:<32}{l}")
    print(f"\nsequential pull -> run -> delete; peak disk ~1 image\n")

    results = []
    t_start = time.time()
    for i, sample in enumerate(picked, 1):
        iid = sample["instance_id"]
        print(f"[{i}/{len(picked)}] {sample['repo']:<30} {sample['repo_language']:<12}", flush=True)
        t0 = time.time()
        try:
            r = evaluate(sample, str(sample["patch"]), args.scripts_dir,
                         os.path.join(args.workspace, f"pilot{i}"),
                         timeout=args.timeout, keep_image=False)
            row = {
                "instance_id": iid, "repo": sample["repo"],
                "language": sample["repo_language"],
                "resolved": r.resolved, "status": r.status,
                "apply_status": r.apply_status, "n_tests_parsed": r.n_tests_parsed,
                "f2p_passed": len(r.f2p_passed), "f2p_missing": len(r.f2p_missing),
                "p2p_broke": len(r.p2p_broke), "seconds": round(time.time() - t0, 1),
                "error": r.error,
            }
        except HarnessError as exc:
            row = {"instance_id": iid, "repo": sample["repo"],
                   "language": sample["repo_language"], "resolved": False,
                   "status": "harness_error", "error": str(exc)[:300],
                   "seconds": round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            row = {"instance_id": iid, "repo": sample["repo"],
                   "language": sample["repo_language"], "resolved": False,
                   "status": f"exception:{type(exc).__name__}", "error": str(exc)[:300],
                   "seconds": round(time.time() - t0, 1)}
        results.append(row)
        mark = "OK " if row["resolved"] else "FAIL"
        print(f"      {mark} status={row['status']} parsed={row.get('n_tests_parsed')} "
              f"f2p={row.get('f2p_passed')}/{row.get('f2p_passed', 0) + row.get('f2p_missing', 0)} "
              f"({row['seconds']}s)", flush=True)
        if row.get("error"):
            print(f"      error: {str(row['error'])[:200]}", flush=True)

    by_lang = defaultdict(lambda: [0, 0])
    for r in results:
        by_lang[r["language"]][1] += 1
        if r["resolved"]:
            by_lang[r["language"]][0] += 1

    resolved = sum(1 for r in results if r["resolved"])
    print(f"\n=== PILOT RESULT: {resolved}/{len(results)} gold patches resolved "
          f"({time.time()-t_start:.0f}s total) ===")
    print(f"{'language':<14}{'resolved':>10}")
    for lang, (ok, tot) in sorted(by_lang.items()):
        print(f"{lang:<14}{ok:>4}/{tot:<4}")
    bad = [r for r in results if not r["resolved"]]
    if bad:
        print("\nrepos where the GOLD patch did not resolve (harness gaps, not model failures):")
        for r in bad:
            print(f"  {r['repo']:<32}{r['status']:<20}{str(r.get('error'))[:80]}")
    else:
        print("\nevery repo grades its own gold patch correctly -- harness is trustworthy "
              "across all languages present.")

    Path(__file__).parent.joinpath(args.out).write_text(
        json.dumps({"n": len(results), "resolved": resolved, "results": results}, indent=2),
        encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
