"""
Harness acceptance test: does it grade a KNOWN answer correctly?

A test harness that has never been shown a correct answer is not a
harness, it is an untested assumption. Two runs on the same instance:

  gold patch   -> MUST report resolved=True.  If it does not, the harness
                  is broken: setup, patch application, test selection or
                  parsing is wrong, and every downstream number would be a
                  false negative.
  empty patch  -> MUST report resolved=False. If it does not, the grader
                  is passing tests that cannot be passing, and every
                  downstream number would be a false positive.

Only if BOTH hold does a resolution rate from this harness mean anything.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pro_harness import evaluate, image_for  # noqa: E402

DEFAULT_SCRIPTS = os.path.expanduser("~/AppData/Local/Temp/swebench_pro_os/run_scripts")


def load_sample(instance_id: str | None) -> dict:
    import pandas as pd

    p = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet"))[0]
    df = pd.read_parquet(p)
    row = (df[df["instance_id"] == instance_id].iloc[0] if instance_id else df.iloc[5])
    return {c: row[c] for c in df.columns}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", default=None)
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS)
    ap.add_argument("--workspace", default=os.path.expanduser("~/AppData/Local/Temp/swebench_ws"))
    ap.add_argument("--timeout", type=int, default=2700)
    args = ap.parse_args()

    sample = load_sample(args.instance_id)
    iid = sample["instance_id"]
    print(f"instance : {iid}")
    print(f"repo     : {sample['repo']}")
    print(f"image    : {image_for(sample)}")
    print(f"scripts  : {os.path.join(args.scripts_dir, iid)}")
    if not os.path.isdir(os.path.join(args.scripts_dir, iid)):
        print("FAIL: run_scripts entry missing for this instance")
        return 1

    outcomes = {}
    for label, patch in (("gold", str(sample["patch"])), ("empty", "")):
        print(f"\n=== running with {label} patch ===")
        t0 = time.time()
        try:
            r = evaluate(sample, patch, args.scripts_dir,
                         os.path.join(args.workspace, label),
                         timeout=args.timeout, keep_image=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {type(exc).__name__}: {str(exc)[:400]}")
            outcomes[label] = None
            continue
        print(f"  resolved     : {r.resolved}")
        print(f"  status       : {r.status}")
        print(f"  apply_status : {r.apply_status}")
        print(f"  tests parsed : {r.n_tests_parsed}")
        print(f"  f2p passed   : {len(r.f2p_passed)}   f2p missing: {len(r.f2p_missing)}")
        print(f"  p2p broken   : {len(r.p2p_broke)}")
        print(f"  exit code    : {r.exit_code}   elapsed {time.time()-t0:.0f}s")
        if r.error:
            print(f"  error        : {r.error[:300]}")
        outcomes[label] = r

    gold, empty = outcomes.get("gold"), outcomes.get("empty")
    ok = bool(gold and gold.resolved) and bool(empty and not empty.resolved)
    print("\n=== VERDICT ===")
    print(f"  gold resolves      : {bool(gold and gold.resolved)}  (must be True)")
    print(f"  empty does not     : {bool(empty and not empty.resolved)}  (must be True)")
    print(f"  HARNESS {'USABLE' if ok else 'NOT YET USABLE'}")

    Path(__file__).parent.joinpath("harness_validation.json").write_text(json.dumps({
        "instance_id": iid,
        "gold": None if not gold else {"resolved": gold.resolved, "status": gold.status,
                                        "apply_status": gold.apply_status,
                                        "n_tests_parsed": gold.n_tests_parsed,
                                        "f2p_passed": len(gold.f2p_passed),
                                        "f2p_missing": gold.f2p_missing[:10],
                                        "p2p_broke": gold.p2p_broke[:10]},
        "empty": None if not empty else {"resolved": empty.resolved, "status": empty.status,
                                          "apply_status": empty.apply_status,
                                          "n_tests_parsed": empty.n_tests_parsed,
                                          "f2p_missing": empty.f2p_missing[:10]},
        "usable": ok,
    }, indent=2), encoding="utf-8")
    print("wrote harness_validation.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
