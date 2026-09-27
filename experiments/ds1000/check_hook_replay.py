"""Is the hook's lookup reproducible? Re-runs round 5's KH hook lookups (no agent, no grading) with round 5's
settings and compares each with what round-5 KH recorded (runs5/episodes.jsonl `hook`). Round 6
(PREREGISTRATION_6.md) reuses round-5 KH as its comparator only if this holds.

    python check_hook_replay.py      # writes runs6/hook_replay.jsonl and prints the agreement
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs6"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
for _flag in ("KNOWLEDGE_VERIFIED_EXAMPLES", "KNOWLEDGE_RELATED_EXAMPLES", "KNOWLEDGE_SUGGESTED_CANDIDATE",
              "FIND_WAYS_GOVERNOR"):
    os.environ[_flag] = "true"

import demo_env  # noqa: E402

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

KEYS = ("outcome", "related_examples", "suggested", "procedures", "context_chars")


def main() -> None:
    demo_env.verify_after_import()
    shutil.copyfile(demo_env.HERE / "runs5" / "design.json", demo_env.RUNS / "design.json")
    from common import problems, test_items
    from models import OPEN_MODELS

    r5 = {}
    for line in (demo_env.HERE / "runs5" / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("arm") == "KH" and e.get("hook"):
            r5[(e["problem_id"], e["model"])] = e["hook"]
    out_path = demo_env.RUNS / "hook_replay.jsonl"
    done = {(r["problem_id"], r["model"]) for r in map(json.loads, out_path.read_text(encoding="utf-8").splitlines())
            } if out_path.exists() else set()
    bridge = kpa.KelBridge(demo_env.DEMO_DSN)
    claims = {m: (demo_env.HERE / "runs4" / f"claims_{m}.md").read_text(encoding="utf-8") for m in OPEN_MODELS}
    jobs = [(t["problem_id"], m) for t in test_items() for m in OPEN_MODELS if (t["problem_id"], m) not in done]

    def one(job):
        pid, m = job
        try:
            _, summary = kpa.hook_context(bridge, problems()[pid]["prompt"], claims[m], session=f"replay:{pid}:{m}")
        except Exception as exc:  # noqa: BLE001 -- infrastructure: re-run next invocation
            print(f"{pid} {m} error {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            return
        rec = {"problem_id": pid, "model": m, "replay": summary, "round5": r5.get((pid, m)),
               "same": {k: summary.get(k) == (r5.get((pid, m)) or {}).get(k) for k in KEYS}}
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"{pid:>4} {m:<16} {summary['outcome']:<10} same={all(rec['same'].values())}", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(one, jobs))
    finally:
        bridge.close()
    rows = [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines()]
    print(f"\n{len(rows)} lookups replayed")
    for k in KEYS:
        print(f"  same {k:<17} {sum(r['same'][k] for r in rows)}/{len(rows)}")
    print(f"  identical on every field: {sum(all(r['same'].values()) for r in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
