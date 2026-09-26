"""Held-out evaluation: five setups replayed over RECORDED, locally graded outcomes.

  1 frontier raw           Sonnet, task statement only
  2 best small raw         the open model with the best FIT-split gold rate (tie: cheaper), task only
  3 small + Kel knowledge  that model with the knowledge Kel's find_ways retrieved
  4 full Kel               knowledge + a model ladder from the REAL recommender (recommend()),
                           re-asked after every rejected rung with that attempt as evidence;
                           a rung is accepted when its VISIBLE tests pass; the answer delivered
                           is graded by the FULL suite
  5 frontier + Kel        Sonnet with the retrieved knowledge

Every model ran once per (task, condition) at temperature 0, so a retry of the same model
reproduces the same outcome -- replaying it is faithful, and it costs again.
Costs: open-model prices are PLACEHOLDERS (prices.json); Sonnet tokens are estimated.

    python evaluate_heldout.py
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

import demo_env

from models import PRICES
from run_models import load_attempts, sample_tasks

logging.disable(logging.INFO)
OPEN = ("gemma-4-31B-it", "gpt-oss-120b", "deepseek-v3.2")
SONNET = "claude-sonnet-5"
SCAFFOLD = {SONNET: "claude-code-subagent"}


def unit(model: str) -> str:
    return f"{model}|{SCAFFOLD.get(model, 'direct-prompt')}"


def table(rows) -> dict:
    solved = sum(1 for r in rows if r["solved"])
    cost = sum(r["cost"] for r in rows)
    return {"tasks": len(rows), "solved": solved, "success_rate": round(solved / max(len(rows), 1), 3),
            "total_cost_usd": round(cost, 5), "cost_per_solved_usd": round(cost / solved, 5) if solved else None,
            "wrong_delivered": sum(1 for r in rows if r.get("wrong")),
            "mean_rungs": round(sum(r.get("rungs", 1) for r in rows) / max(len(rows), 1), 2)}


TARGETS = (0.5, 0.6, 0.7, 0.8, 0.9)


async def full_kel(pool, attempts, knowledge, tasks, condition, target: float = 0.9) -> list[dict]:
    from app.routing import service, store
    from app.services.access import AccessScope

    for model, price in PRICES.items():
        if not model.startswith("_"):
            await store.set_price(pool, model, input_per_mtok=price["input"], output_per_mtok=price["output"])
    domain_goal = {r["canonical_name"]: r["id"] for r in await pool.fetch(
        "SELECT goal_id::text AS id, canonical_name FROM goal_search_index")}
    rows = []
    for task_id, task in tasks.items():
        k = knowledge.get(task_id, {})
        proc = k.get("ref")
        goal = None
        if proc:
            goal = await pool.fetchval(
                "SELECT achieves_goal_id::text FROM procedures WHERE procedure_id = $1::uuid LIMIT 1", proc)
        goal = goal or domain_goal.get(task.domains[0])          # no knowledge: the task's domain Goal
        tried, cost, solved, wrong, rungs, trace = [], 0.0, False, False, 0, []
        while rungs < 3:
            rec = await service.recommend(
                pool, goal_id=goal, procedure_id=proc, access_scope=AccessScope.anonymous(),
                candidates=[unit(m) for m in (*OPEN, SONNET)], check_kind="tests",
                instance_key=f"heldout:{condition}:{target}:{task_id}", previous_attempts=tried, record=True,
                constraints={"reliability_target": target, "allow_retries": False})
            if rec.get("status") != "ok" or not rec.get("recommended"):
                trace.append({"recommend": rec.get("status"), "reason": rec.get("reason")})
                break
            model = rec["recommended"]["ladder"][0].split("|")[0]
            a = attempts.get((task_id, model, condition))
            if a is None:
                trace.append({"missing_attempt": model})
                break
            rungs += 1
            cost += a["cost_usd"]
            trace.append({"model": model, "visible": a["visible_pass"], "gold": a["gold_pass"],
                          "p_success": rec["recommended"]["p_success"]})
            if a["visible_pass"]:
                solved, wrong = bool(a["gold_pass"]), not a["gold_pass"]
                break
            tried.append({"unit": unit(model), "accepted": False, "check_kind": "tests"})
        rows.append({"task": task_id, "solved": solved, "wrong": wrong, "cost": cost, "rungs": rungs, "trace": trace})
    return rows


async def main(condition: str) -> None:
    demo_env.verify_after_import()
    from app.db.session import create_pool

    tasks = sample_tasks("heldout")
    kfile = "knowledge_heldout.json" if condition == "kel" else "knowledge_heldout_down.json"
    knowledge = json.loads((demo_env.RUNS / kfile).read_text(encoding="utf-8"))
    all_attempts = load_attempts()
    attempts = {(r["task_id"], r["model"], r["condition"]): r for r in all_attempts if r["split"] == "heldout"}
    fit = defaultdict(list)
    for r in all_attempts:
        if r["split"] == "fit" and r["condition"] == "raw" and r["model"] in OPEN:
            fit[r["model"]].append(r)
    best_small = max(OPEN, key=lambda m: (sum(r["gold_pass"] for r in fit[m]),
                                          -(PRICES[m]["input"] + PRICES[m]["output"])))

    def single(model, condition):
        rows = []
        for t in tasks:
            a = attempts.get((t, model, condition))
            if a is None:
                raise SystemExit(f"missing attempt {t} {model} {condition}: run the held-out runs first")
            rows.append({"task": t, "solved": bool(a["gold_pass"]), "cost": a["cost_usd"]})
        return rows

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        by_target = {t: await full_kel(pool, attempts, knowledge, tasks, condition, t) for t in TARGETS}
        kel_rows = by_target[0.9]
    finally:
        await pool.close()
    report = {
        "held_out_tasks": len(tasks),
        "tasks_with_retrieved_knowledge": sum(1 for t in tasks if knowledge.get(t, {}).get("ref")),
        "best_small_model_chosen_on_fit": best_small,
        "setups": {
            "1_frontier_raw": table(single(SONNET, "raw")),
            "2_best_small_raw": table(single(best_small, "raw")),
            "3_small_plus_kel_knowledge": table(single(best_small, condition)),
            "4_full_kel_knowledge_plus_routing": table(kel_rows),
            "4_full_kel_by_reliability_target": {str(t): {**table(rows),
                "first_rung_models": dict(__import__("collections").Counter(
                    r["trace"][0].get("model") for r in rows if r["trace"]))} for t, rows in by_target.items()},
            "5_frontier_plus_kel_knowledge": table(single(SONNET, condition)),
        },
        "retrieval": condition, "every_model": {m: {c: table(single(m, c)) for c in ("raw", condition)} for m in (*OPEN, SONNET)},
        "caveats": [
            "20 held-out tasks: a pipeline test, not a statistically meaningful result",
            "open-model prices are placeholders (prices.json); Sonnet tokens estimated from text length",
            "Sonnet ran as a Claude Code subagent, not the raw API",
            "tasks limited to pure-computation libraries runnable locally",
        ],
        "full_kel_traces": kel_rows,
    }
    (demo_env.RUNS / f"report_{condition}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("full_kel_traces",)}, indent=1, default=str))


if __name__ == "__main__":
    import sys
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "kel"))
