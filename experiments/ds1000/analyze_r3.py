"""Round 3's preregistered analysis (PREREGISTRATION_3.md). Primary: pooled K - A on transfer tasks.

    KEL_DS1000_RUNS=runs3 python analyze_r3.py      # writes runs3/report.json
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter

import demo_env

from analyze import TARGETS, holm, paired, routing, summary
from common import DOMAINS, load_attempts, test_items
from models import OPEN_MODELS, PRICES, SONNET


async def main() -> None:
    demo_env.verify_after_import()
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in load_attempts() if not r.get("call_error")}
    gold = lambda m, arm: {pid: r["gold_pass"] for (pid, mm, a), r in att.items() if mm == m and a == arm}
    pt = lambda ts: [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in ts for m in OPEN_MODELS]
    pg = lambda arm: {f"{pid}|{m}": v for m in OPEN_MODELS for pid, v in gold(m, arm).items()}
    notes = {k: json.loads((demo_env.RUNS / f"notes_{k}.json").read_text(encoding="utf-8")) for k in ("B", "K", "E")}

    rep: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip()}
    primary = paired(pt(transfer), pg("A"), pg("K"))
    primary["confirmed"] = bool(primary["ci95"][0] > 0 and primary["mcnemar_p"] < 0.05)
    rep["primary_pooled_K_minus_A_transfer"] = primary
    per = {m: paired(transfer, gold(m, "A"), gold(m, "K")) for m in OPEN_MODELS}
    for m, p in holm({m: v["mcnemar_p"] for m, v in per.items()}).items():
        per[m]["holm_p"] = round(p, 4)
    rep["per_model_K_minus_A_transfer"] = per
    non_surface = [t for t in transfer if t["perturbation"] != "Surface"]
    rep["secondary"] = {
        "K-E": paired(pt(transfer), pg("E"), pg("K")),
        "K-B": paired(pt(transfer), pg("B"), pg("K")),
        "B-A": paired(pt(transfer), pg("A"), pg("B")),
        "E-A": paired(pt(transfer), pg("A"), pg("E")),
        "K-A_non_surface": paired(pt(non_surface), pg("A"), pg("K")),
        "K-A_by_type": {ty: paired(pt([t for t in transfer if t["perturbation"] == ty]), pg("A"), pg("K"))
                        for ty in ("Surface", "Semantic", "Difficult-Rewrite")},
        "control_K-A": paired(pt(control), pg("A"), pg("K")),
        "lost_all_tasks_K": {m: sum(1 for t in tasks if gold(m, "A").get(t["problem_id"]) and
                                    gold(m, "K").get(t["problem_id"]) is False) for m in OPEN_MODELS},
        "gained_all_tasks_K": {m: sum(1 for t in tasks if gold(m, "A").get(t["problem_id"]) is False and
                                      gold(m, "K").get(t["problem_id"])) for m in OPEN_MODELS},
        "sonnet_K-A_transfer": paired(transfer, gold(SONNET, "A"), gold(SONNET, "K")),
    }
    # retrieval: coverage + own-family precision (procedure or any related example from the task's family)
    fam_of: dict[str, str] = {f["problem_id"]: f["family"] for f in json.loads(
        (demo_env.RUNS / "design.json").read_text(encoding="utf-8"))["fit"]}
    proc_src = {p["procedure_id"]: pid for pid, p in json.loads(
        (demo_env.RUNS / "procedures_fit.json").read_text(encoding="utf-8")).items()}

    def cover(arm: str, role_tasks: list) -> dict:
        n = notes[arm]
        given = [t for t in role_tasks if n.get(t["problem_id"], {}).get("text")]
        if arm == "E":
            own = sum(1 for t in given if n[t["problem_id"]].get("same_family"))
        else:
            ids = lambda v: [v.get("procedure")] * bool(v.get("procedure")) + list(v.get("related") or []) \
                if arm == "K" else [v.get("ref")]
            own = sum(1 for t in given if any(fam_of.get(proc_src.get(str(i))) == t["family"] for i in ids(n[t["problem_id"]])))
        return {"given": len(given), "of": len(role_tasks), "any_note_from_own_family": own}
    rep["retrieval"] = {f"{arm}_{name}": cover(arm, ts) for arm in ("B", "K", "E")
                        for name, ts in (("transfer", transfer), ("control", control))}
    rep["retrieval"]["K_paths"] = dict(Counter(" -> ".join(p["outcome"] for p in v["path"]) for v in notes["K"].values()))

    from app.db.session import create_pool
    from app.routing import store

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        for model, price in PRICES.items():
            if not model.startswith("_"):
                await store.set_price(pool, model, input_per_mtok=price["input"], output_per_mtok=price["output"])
        # Round 3's knowledge base has no library-level Goals. A task with no Kel Procedure is routed on a
        # generic Goal with NO data of its own, so the recommender answers from its population prior --
        # what production does for an unseen Goal (analysis-time only; nothing else reads it).
        from app.services.goals import find_or_create_goal

        generic = await find_or_create_goal(pool, canonical_name="Unclassified Python data-science task",
                                            description=None, scope_type="global", provenance="prior_library",
                                            status="active", judge_mode="none", embedder=None)
        from app.services import search_projection as sp

        await sp.drain_outbox(pool)          # the routing store reads the Goal projection
        domain_goal = {d: str(generic["id"]) for d in DOMAINS.values()}
        s6 = {"sonnet_A": summary([{"solved": att[(t["problem_id"], SONNET, "A")]["gold_pass"],
                                    "cost": att[(t["problem_id"], SONNET, "A")]["cost_usd"]} for t in tasks])}
        for target in TARGETS:
            for arm, label in (("K", "F_K_knowledge_plus_routing"), ("A", "F0_routing_only")):
                rows = await routing(pool, att, tasks, notes["K"], arm, target, domain_goal)
                s6[f"{label}@{target}"] = {**summary(rows), "first_model": dict(Counter(
                    r["trace"][0].get("model") for r in rows if r["trace"]))}
        rep["S8_routing"] = s6
    finally:
        await pool.close()
    rep["notes"] = ["open-model prices are placeholders; Sonnet tokens estimated (chars/4)",
                    "routing for tasks with no Kel Procedure uses a generic Goal with no data (population prior)"]
    (demo_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "S8_routing"}, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
