"""
Two-run sanity check on one instance, before anything expensive runs.

Gold patch must resolve and the empty patch must not. Only the pair is
informative: gold-resolves alone would still pass if the harness marked
everything resolved, and empty-fails alone would still pass if the harness
were simply broken. Together they show the harness can tell the two apart,
which is the minimum for any accuracy number downstream to mean anything.
"""
from __future__ import annotations

import os
import sys
import time

from datasets import load_dataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pro_harness import evaluate  # noqa: E402

SCRIPTS = os.environ["PRO_SCRIPTS_DIR"]
WORK = os.environ.get("PRO_WORK", os.path.join(os.path.dirname(__file__), "_work"))


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    rows = [r for r in ds if r["repo"] == "ansible/ansible"]
    sample = next((r for r in rows if r["instance_id"] == target), rows[0])

    print(f"instance : {sample['instance_id']}")
    print(f"base     : {sample['base_commit']}")
    print(f"tests    : {sample['selected_test_files_to_run']}")
    print(f"patch    : {len(sample['patch'])} bytes\n")

    for label, patch in (("GOLD", sample["patch"]), ("EMPTY", "")):
        t0 = time.time()
        # keep_image: the two runs are the same instance, so paying the pull
        # once instead of twice is free correctness-wise.
        res = evaluate(sample, patch, SCRIPTS,
                       os.path.join(WORK, sample["instance_id"], label.lower()),
                       keep_image=(label == "GOLD"))
        print(f"[{label:5s}] {res.summary()}  ({time.time() - t0:.0f}s)")
        if res.error:
            print(f"         error: {res.error}")
        if label == "GOLD" and not res.resolved:
            print("\n  gold did not resolve -- harness is wrong, not the model.")
            print(f"  apply_status={res.apply_status} exit={res.exit_code}")
            print(f"  f2p_missing (first 5): {res.f2p_missing[:5]}")
            _tail(os.path.join(WORK, sample["instance_id"], "gold"))


def _tail(ws: str) -> None:
    for name in ("setup.log", "stderr.log", "stdout.log"):
        path = os.path.join(ws, name)
        if os.path.exists(path):
            body = open(path, encoding="utf-8", errors="replace").read()
            print(f"\n--- {name} (last 1500) ---\n{body[-1500:]}")


if __name__ == "__main__":
    main()
