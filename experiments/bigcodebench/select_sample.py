"""Pick the small-test sample: N fit + M held-out tasks from the locally runnable subset,
stratified by primary domain, deterministic (hash of seed + task id).

Harness validity: a task is kept only if its tests pass with the dataset's reference
solution in THIS environment (as BigCodeBench's own harness checks), so an environment
quirk is never scored as a model failure. The reference is used in memory only.

    python select_sample.py --fit 40 --heldout 20
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import demo_env  # noqa: F401  -- isolation first

from app.benchmarks import bigcodebench as bcb
from app.benchmarks.tasks import assign_splits, stable_hash
from evaluate import run_tests
from safety import runnable_locally


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", type=int, default=40)
    ap.add_argument("--heldout", type=int, default=20)
    ap.add_argument("--seed", default="kel-demo-1")
    a = ap.parse_args()
    rows = {r["task_id"]: r for r in bcb.load_rows(demo_env.DATA / f"bigcodebench-{bcb.LATEST_VERSION}.parquet")}
    tasks = bcb.tasks_from_rows(rows.values())
    assign_splits(tasks)
    chosen: dict[str, list[str]] = {}
    report = {}
    for split, want in (("fit", a.fit), ("heldout", a.heldout)):
        pool = [t for t in tasks if t.split == split and t.excluded_reason is None
                and runnable_locally(t.libs, t.test_code)[0]]
        by_domain = defaultdict(list)
        for t in sorted(pool, key=lambda t: stable_hash(a.seed, t.external_id)):
            by_domain[t.domains[0]].append(t)
        order = []                                   # round-robin over domains = stratified
        while any(by_domain.values()):
            for d in sorted(by_domain):
                if by_domain[d]:
                    order.append(by_domain[d].pop(0))
        picked, invalid = [], []
        for t in order:
            if len(picked) >= want:
                break
            r = rows[t.external_id]
            res = run_tests(r["code_prompt"] + r["canonical_solution"], t.test_code, t.visible_tests)
            (picked if res["gold_pass"] else invalid).append(t.external_id)
        chosen[split] = picked
        report[split] = {"picked": len(picked), "invalid_in_this_environment": invalid}
    (demo_env.RUNS).mkdir(parents=True, exist_ok=True)
    (demo_env.RUNS / "sample.json").write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
