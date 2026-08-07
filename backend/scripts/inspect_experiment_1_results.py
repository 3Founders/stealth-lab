"""
Quick diagnostic: shows ground truth vs actual top-3 ranking per task,
from a run_experiment_1.py output. Run from backend/ after a pilot or
full run:
    python scripts/inspect_experiment_1_results.py
"""
import json

results = json.load(open("experiment_1_results.json"))
tasks = json.load(open("after_task_index.json"))
tasks_by_id = {t["id"]: t for t in tasks}

for tid, ranked in results["per_task_rankings"].items():
    t = tasks_by_id[tid]
    gt = set(t["skills"])
    top3 = ranked[:3]
    top1_hit = top3[0][0] in gt if top3 else False
    gt_rank = next((i + 1 for i, (s, _) in enumerate(ranked) if s in gt), None)

    marker = "HIT " if top1_hit else "MISS"
    print(f"[{marker}] {tid}")
    print(f"        ground truth: {sorted(gt)}")
    print(f"        top-3 ranked: {[(s, round(sc, 3)) for s, sc in top3]}")
    print(f"        true skill's actual rank: {gt_rank if gt_rank else 'not in top ' + str(len(ranked))}")
    print()
