"""Re-price recorded routing results with General Compute's published prices (2026-09-28).

    python reprice_routing.py      # prints old vs new cost per setup; changes no recorded file

The routing decisions (which model tried each task, in which order) are replayed exactly as recorded in
ds1000/runs*/routing_traces.json; only the price of each rung changes, recomputed from that attempt's recorded
tokens. Validation first: re-pricing with the OLD table must reproduce every recorded cost. Caveat: the
recommender chose those ladders under the placeholder prices; with real prices it might choose differently.

Prices (USD per 1M tokens), https://docs.generalcompute.com/models.md, read 2026-09-28:
  deepseek-v3.2 0.25 / 0.38; gpt-oss-120b 0.21 / 0.79. gemma-4-31B-it is not in General Compute's table:
  it keeps the placeholder (0.10 / 0.30) and every result states how much of its cost is gemma's.
  claude-sonnet-5: Anthropic list price 2.00 / 10.00 (unchanged).
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The placeholder table every result before 2026-09-28 was priced with (bigcodebench/prices.json then).
OLD = {"gemma-4-31B-it": {"input": 0.10, "output": 0.30}, "gpt-oss-120b": {"input": 0.15, "output": 0.60},
       "deepseek-v3.2": {"input": 0.28, "output": 0.42}, "claude-sonnet-5": {"input": 2.00, "output": 10.00}}
NEW = {**OLD, "deepseek-v3.2": {"input": 0.25, "output": 0.38}, "gpt-oss-120b": {"input": 0.21, "output": 0.79}}
ARM_OF = {"F": "B", "Fc": "Bc", "F0": "A", "Fb": "Bw", "Fw": "Bw"}


def cost(prices, model, tin, tout):
    p = prices[model]
    return (tin * p["input"] + tout * p["output"]) / 1e6


def attempts(runs: Path) -> dict:
    out = {}
    for line in (runs / "attempts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if not r.get("call_error"):
                out[(r["problem_id"], r["model"], r["arm"])] = r
    return out


def reprice(runs: Path) -> list[dict]:
    traces = json.loads((runs / "routing_traces.json").read_text(encoding="utf-8"))
    att = attempts(runs)
    rows = []
    for setup, items in traces.items():
        arm = ARM_OF.get(setup.split("_")[0])
        old_total = new_total = recorded = gemma = 0.0
        mismatch = 0
        for it in items:
            recorded += it["cost"]
            o = n = 0.0
            for rung in it["trace"]:
                r = att.get((it["problem_id"], rung["model"], arm)) or att.get((it["problem_id"], rung["model"], "A"))
                o += cost(OLD, rung["model"], r["tokens_in"], r["tokens_out"])
                c = cost(NEW, rung["model"], r["tokens_in"], r["tokens_out"])
                n += c
                if rung["model"].startswith("gemma"):
                    gemma += c
            mismatch += abs(o - it["cost"]) > 1e-6
            old_total += o
            new_total += n
        rows.append({"setup": setup, "tasks": len(items), "solved": sum(i["solved"] for i in items),
                     "recorded": recorded, "old": old_total, "new": new_total, "gemma_share": gemma / new_total if new_total else 0,
                     "mismatches": mismatch})
    return rows


def main() -> None:
    for runs in (HERE / "ds1000" / "runs", HERE / "ds1000" / "runs2"):
        att = attempts(runs)
        tids = json.loads((runs / "routing_traces.json").read_text(encoding="utf-8"))
        first = next(iter(tids.values()))
        pids = {i["problem_id"] for i in first}
        son = [r for (p, m, a), r in att.items() if m == "claude-sonnet-5" and a == "A" and p in pids]
        son_cost = sum(cost(NEW, "claude-sonnet-5", r["tokens_in"], r["tokens_out"]) for r in son)
        print(f"\n== {runs.parent.name}/{runs.name}: Sonnet alone {sum(r['gold_pass'] for r in son)}/{len(son)} solved, ${son_cost:.4f}")
        print(f"{'setup':<40}{'solved':>8}{'old $':>10}{'new $':>10}{'new vs Sonnet':>15}{'gemma share':>13}{'mismatch':>9}")
        for r in reprice(runs):
            print(f"{r['setup']:<40}{r['solved']:>5}/{r['tasks']:<3}{r['old']:>10.4f}{r['new']:>10.4f}"
                  f"{(r['new'] / son_cost - 1):>+14.0%}{r['gemma_share']:>12.0%}{r['mismatches']:>9}")


if __name__ == "__main__":
    main()
