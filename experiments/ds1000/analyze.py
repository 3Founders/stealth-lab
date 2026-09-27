"""The preregistered analysis (PREREGISTRATION.md). Reads runs/attempts.jsonl and the notes;
writes runs/report.json and prints a summary.

    python analyze.py
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from collections import Counter, defaultdict

import numpy as np

import demo_env

from common import DOMAINS, load_attempts, problems, test_items
from models import OPEN_MODELS, PRICES, SONNET

SCAFFOLD = {SONNET: "claude-code-subagent"}
TARGETS = (0.5, 0.6, 0.7, 0.8, 0.9)
BOOT = 10_000


def unit(m: str) -> str:
    return f"{m}|{SCAFFOLD.get(m, 'direct-prompt')}"


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    order = sorted(pvals, key=pvals.get)
    out, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (len(order) - i) * pvals[k]))
        out[k] = running
    return out


def paired(tasks: list[dict], x: dict[str, bool], y: dict[str, bool], seed: int = 0) -> dict:
    """y - x over `tasks` (both must exist), family-clustered bootstrap CI + exact McNemar."""
    ts = [t for t in tasks if t["problem_id"] in x and t["problem_id"] in y]
    if not ts:
        return {"n": 0}
    d = {t["problem_id"]: int(y[t["problem_id"]]) - int(x[t["problem_id"]]) for t in ts}
    fams = defaultdict(list)
    for t in ts:
        fams[t["family"]].append(d[t["problem_id"]])
    keys = list(fams)
    rng = np.random.default_rng(seed)
    sums = np.array([sum(fams[k]) for k in keys], dtype=float)
    cnts = np.array([len(fams[k]) for k in keys], dtype=float)
    idx = rng.integers(0, len(keys), size=(BOOT, len(keys)))
    boot = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    b = sum(1 for v in d.values() if v == 1)
    c = sum(1 for v in d.values() if v == -1)
    return {"n": len(ts), "x_rate": round(sum(x[t["problem_id"]] for t in ts) / len(ts), 3),
            "y_rate": round(sum(y[t["problem_id"]] for t in ts) / len(ts), 3),
            "delta": round(sum(d.values()) / len(ts), 3),
            "ci95": [round(float(np.quantile(boot, 0.025)), 3), round(float(np.quantile(boot, 0.975)), 3)],
            "gained": b, "lost": c, "mcnemar_p": round(mcnemar_exact(b, c), 4)}


def api_names(code: str) -> set[str]:
    return {m for m in re.findall(r"\.([A-Za-z_]\w{2,})\s*\(", code)} | set(re.findall(r"\b([a-z_]{3,}\w*)\s*\(", code))


async def routing(pool, attempts, tasks, notes_b, arm: str, target: float, domain_goal: dict) -> list[dict]:
    from app.routing import service
    from app.services.access import AccessScope

    rows = []
    for t in tasks:
        pid = t["problem_id"]
        proc = notes_b.get(pid, {}).get("procedure", notes_b.get(pid, {}).get("ref")) if arm != "A" else None
        goal = None
        if proc:
            goal = await pool.fetchval(
                "SELECT achieves_goal_id::text FROM procedures WHERE procedure_id = $1::uuid LIMIT 1", proc)
        goal = goal or domain_goal[DOMAINS[t["library"]]]
        tried, cost, solved, wrong, rungs, trace = [], 0.0, False, False, 0, []
        while rungs < 3:
            rec = await service.recommend(
                pool, goal_id=goal, procedure_id=proc, access_scope=AccessScope.anonymous(),
                candidates=[unit(m) for m in (*OPEN_MODELS, SONNET)], check_kind="tests",
                instance_key=f"test:{target}:{pid}", previous_attempts=tried, record=False,
                constraints={"reliability_target": target, "allow_retries": False})
            if rec.get("status") != "ok" or not rec.get("recommended"):
                trace.append({"recommend": rec.get("status"), "reason": str(rec.get("reason"))[:120]})
                break
            model = rec["recommended"]["ladder"][0].split("|")[0]
            a = attempts.get((pid, model, arm))
            if a is None:
                trace.append({"missing_attempt": model})
                break
            rungs += 1
            cost += a["cost_usd"]
            trace.append({"model": model, "check": a["check_pass"], "gold": a["gold_pass"]})
            if a["check_pass"]:
                solved, wrong = bool(a["gold_pass"]), not a["gold_pass"]
                break
            tried.append({"unit": unit(model), "accepted": False, "check_kind": "tests"})
        rows.append({"problem_id": pid, "solved": solved, "wrong": wrong, "cost": cost, "rungs": rungs, "trace": trace})
    return rows


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    solved = sum(r["solved"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    return {"tasks": n, "solved": solved, "rate": round(solved / max(n, 1), 3), "cost_usd": round(cost, 5),
            "cost_per_solved": round(cost / solved, 6) if solved else None,
            "wrong_delivered": sum(r.get("wrong", False) for r in rows),
            "mean_attempts": round(sum(r.get("rungs", 1) for r in rows) / max(n, 1), 2)}


async def main() -> None:
    demo_env.verify_after_import()
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    raw = [r for r in load_attempts() if not r.get("call_error")]
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in raw}
    gold = lambda m, arm: {pid: r["gold_pass"] for (pid, mm, a), r in att.items() if mm == m and a == arm}
    notes = {k: (json.loads((demo_env.RUNS / f"notes_{k}.json").read_text(encoding="utf-8"))
                 if (demo_env.RUNS / f"notes_{k}.json").exists() else {}) for k in ("B", "C", "D", "E", "Bc", "Cc", "Bw")}
    PRIMARY = next((sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == "--primary"), "B")
    fam_origin = {}
    for f in json.loads((demo_env.RUNS / "design.json").read_text(encoding="utf-8"))["fit"]:
        fam_origin[f["family"]] = f["problem_id"]
    procs = json.loads((demo_env.RUNS / "procedures_fit.json").read_text(encoding="utf-8"))
    proc_to_fit = {v["procedure_id"]: k for k, v in procs.items()}
    fit_family = {f_pid: fam for fam, f_pid in fam_origin.items()}

    report: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip()}
    # --- primary
    prim, pv = {}, {}
    for m in OPEN_MODELS:
        prim[m] = paired(transfer, gold(m, "A"), gold(m, PRIMARY))
        pv[m] = prim[m].get("mcnemar_p", 1.0)
    for m, p in holm(pv).items():
        prim[m]["holm_p"] = round(p, 4)
    report[f"primary_{PRIMARY}_minus_A_transfer"] = prim

    # --- code arms (round-1 Addendum 1; round 2's primary)
    if any(k[2] in ("Bc", "Cc") for k in att):
        pairs = {"Bc-A": ("A", "Bc"), "Bc-B": ("B", "Bc"), "Bc-E": ("E", "Bc"), "B-A": ("A", "B"), "E-A": ("A", "E"),
                 "Cc-A": ("A", "Cc"), "Cc-C": ("C", "Cc"), "Cc-Bc": ("Bc", "Cc"),
                 "Bw-A": ("A", "Bw"), "Bw-E": ("E", "Bw"), "Bw-Bc": ("Bc", "Bw")}
        sc = {m: {k: paired(transfer, gold(m, x), gold(m, y)) for k, (x, y) in pairs.items()} for m in OPEN_MODELS}
        pt = [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in transfer for m in OPEN_MODELS]
        pg = lambda arm: {f"{pid}|{m}": v for m in OPEN_MODELS for pid, v in gold(m, arm).items()}
        sc["pooled_open_models"] = {k: paired(pt, pg(x), pg(y)) for k, (x, y) in pairs.items()}
        sc["pooled_by_type_Bc-A"] = {ty: paired([x for x in pt if x["perturbation"] == ty], pg("A"), pg("Bc"))
                                      for ty in ("Surface", "Semantic", "Difficult-Rewrite")}
        sc["sonnet_Bc-A"] = {"transfer": paired(transfer, gold(SONNET, "A"), gold(SONNET, "Bc")),
                             "control": paired(control, gold(SONNET, "A"), gold(SONNET, "Bc"))}
        sc["control_Bc-A"] = {m: paired(control, gold(m, "A"), gold(m, "Bc")) for m in OPEN_MODELS}
        sc["lost_all_tasks_Bc"] = {m: sum(1 for t in tasks if gold(m, "A").get(t["problem_id"]) and
                                          gold(m, "Bc").get(t["problem_id"]) is False) for m in OPEN_MODELS}
        sc["gained_all_tasks_Bc"] = {m: sum(1 for t in tasks if gold(m, "A").get(t["problem_id"]) is False and
                                            gold(m, "Bc").get(t["problem_id"])) for m in OPEN_MODELS}
        report["S_code"] = sc

    # --- S1 arms vs A and B vs E, per model and pooled
    s1 = {}
    for m in OPEN_MODELS:
        s1[m] = {"C-A": paired(transfer, gold(m, "A"), gold(m, "C")),
                 "D-A": paired(transfer, gold(m, "A"), gold(m, "D")),
                 "E-A": paired(transfer, gold(m, "A"), gold(m, "E")),
                 "B-E": paired(transfer, gold(m, "E"), gold(m, "B")),
                 "C-B": paired(transfer, gold(m, "B"), gold(m, "C"))}
    pooled_tasks = [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in transfer for m in OPEN_MODELS]
    pool_g = lambda arm: {f"{pid}|{m}": v for m in OPEN_MODELS for pid, v in gold(m, arm).items()}
    s1["pooled_open_models"] = {k: paired(pooled_tasks, pool_g(x), pool_g(y)) for k, (x, y) in
                                {"B-A": ("A", "B"), "C-A": ("A", "C"), "D-A": ("A", "D"), "E-A": ("A", "E"),
                                 "B-E": ("E", "B"), "C-B": ("B", "C")}.items()}
    report["S1_arms"] = s1

    # --- S2 by perturbation type
    report["S2_B_minus_A_by_type"] = {
        ptype: {m: paired([t for t in transfer if t["perturbation"] == ptype], gold(m, "A"), gold(m, "B"))
                for m in OPEN_MODELS} for ptype in ("Surface", "Semantic", "Difficult-Rewrite")}
    report["S2_B_minus_A_by_type_pooled"] = {
        ptype: paired([x for x in pooled_tasks if x["perturbation"] == ptype], pool_g("A"), pool_g("B"))
        for ptype in ("Surface", "Semantic", "Difficult-Rewrite")}

    # --- S3 control
    report["S3_control"] = {
        "given_notes": sum(1 for t in control if notes["B"].get(t["problem_id"], {}).get("ref")), "of": len(control),
        **{m: paired(control, gold(m, "A"), gold(m, "B")) for m in OPEN_MODELS},
        "lost_all_tasks": {m: sum(1 for t in tasks if gold(m, "A").get(t["problem_id"]) and
                                  gold(m, "B").get(t["problem_id"]) is False) for m in OPEN_MODELS}}

    # --- S4 Sonnet
    report["S4_sonnet_B_minus_A"] = {"transfer": paired(transfer, gold(SONNET, "A"), gold(SONNET, "B")),
                                     "control": paired(control, gold(SONNET, "A"), gold(SONNET, "B"))}

    # --- S5 retrieval coverage and precision
    def precision(role_tasks, arm):
        given = [t for t in role_tasks if notes[arm].get(t["problem_id"], {}).get("ref")]
        if arm == "E":
            right = sum(1 for t in given if notes["E"][t["problem_id"]].get("same_family"))
        else:
            right = sum(1 for t in given if fit_family.get(proc_to_fit.get(notes[arm][t["problem_id"]]["ref"])) == t["family"])
        return {"given": len(given), "of": len(role_tasks), "from_own_family": right}
    report["S5_retrieval"] = {"B_transfer": precision(transfer, "B"), "B_control": precision(control, "B"),
                              "E_transfer": precision(transfer, "E"),
                              "B_paths": dict(Counter(" -> ".join(p["outcome"] for p in v["path"])
                                                      for v in notes["B"].values()))}

    # --- S7 noise
    report["S7_noise_A_vs_A2"] = {m: {"flips": sum(1 for pid, v in gold(m, "A").items() if gold(m, "A2").get(pid, v) != v),
                                      "n": len(gold(m, "A2"))} for m in OPEN_MODELS}

    # --- S8 leakage audit
    s8 = {}
    for arm in ("B", "C", "D", "E", "Bc", "Cc"):
        fr = []
        for t in transfer:
            n_ = notes[arm].get(t["problem_id"], {})
            if not n_.get("text"):
                continue
            ref_apis = api_names(problems()[t["problem_id"]]["reference_code"])
            if ref_apis:
                fr.append(sum(1 for a in ref_apis if a in n_["text"]) / len(ref_apis))
        s8[arm] = {"n": len(fr), "mean_share_of_reference_calls_named_in_notes": round(sum(fr) / len(fr), 3) if fr else None}
    report["S8_leakage"] = s8

    # --- S6 routing: F (knowledge + routing) and F0 (routing only) vs Sonnet-A
    from app.db.session import create_pool
    from app.routing import store

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        for model, price in PRICES.items():
            if not model.startswith("_"):
                await store.set_price(pool, model, input_per_mtok=price["input"], output_per_mtok=price["output"])
        domain_goal = {r["canonical_name"]: r["id"] for r in await pool.fetch(
            "SELECT goal_id::text AS id, canonical_name FROM goal_search_index WHERE canonical_name = ANY($1::text[])",
            list(DOMAINS.values()))}
        s6 = {"sonnet_A": summary([{"solved": att[(t["problem_id"], SONNET, "A")]["gold_pass"],
                                    "cost": att[(t["problem_id"], SONNET, "A")]["cost_usd"]} for t in tasks
                                   if (t["problem_id"], SONNET, "A") in att])}
        for m in OPEN_MODELS:
            s6[f"{m}_A"] = summary([{"solved": att[(t["problem_id"], m, "A")]["gold_pass"],
                                     "cost": att[(t["problem_id"], m, "A")]["cost_usd"]} for t in tasks])
        traces = {}
        for target in TARGETS:
            for arm, label in (("B", "F_knowledge_plus_routing"), ("Bc", "Fc_code_knowledge_plus_routing"),
                               ("A", "F0_routing_only")):
                if not any(k[1] == SONNET and k[2] == arm for k in att):
                    continue
                rows = await routing(pool, att, tasks, notes["B"], arm, target, domain_goal)
                s6[f"{label}@{target}"] = {**summary(rows), "first_model": dict(Counter(
                    r["trace"][0].get("model") for r in rows if r["trace"]))}
                traces[f"{label}@{target}"] = rows
        report["S6_routing"] = s6
    finally:
        await pool.close()
    report["costs_note"] = "open-model prices are placeholders; Sonnet tokens estimated (chars/4)"
    (demo_env.RUNS / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (demo_env.RUNS / "routing_traces.json").write_text(json.dumps(traces, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()}, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
