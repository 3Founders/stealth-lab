"""Round 5's preregistered analysis (PREREGISTRATION_5.md). Primary: pooled KH - AG5 on transfer tasks.

    python analyze_r5.py      # writes runs5/report.json
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs5"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"

import demo_env  # noqa: E402

import json  # noqa: E402

from analyze import holm, paired  # noqa: E402
from common import load_attempts, test_items  # noqa: E402
from models import OPEN_MODELS  # noqa: E402

ARMS = ("AG5", "KP5", "KH")


def count(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def usage(eps: list[dict]) -> dict:
    """What Kel delivered: the agent's own find_ways calls, and (KH) the hook's lookup."""
    fw = [[c for c in (e.get("kel_calls") or []) if c["tool"] == "find_ways"] for e in eps]
    calls = [c for cs in fw for c in cs]
    n = max(len(eps), 1)
    u = {"episodes": len(eps), "called_find_ways": sum(bool(cs) for cs in fw),
         "find_ways_calls": len(calls),
         "find_ways_outcomes": count(c.get("outcome") for c in calls),
         "governor": count(c.get("governor") for c in calls if c.get("governor")),
         "calls_with_related_examples": sum(1 for c in calls if c.get("related_examples")),
         "related_examples_received": sum(c.get("related_examples") or 0 for c in calls),
         "calls_with_suggested": sum(1 for c in calls if c.get("suggested")),
         "episodes_got_verified_solution_from_call": sum(any(c.get("verified_solution") for c in cs) for cs in fw),
         "mean_kel_calls": round(sum(len(e.get("kel_calls") or []) for e in eps) / n, 2),
         "mean_steps": round(sum(e.get("steps") or 0 for e in eps) / n, 2)}
    hooks = [e["hook"] for e in eps if e.get("hook")]
    if hooks:
        chars = sorted(h.get("context_chars") or 0 for h in hooks)
        u["hook"] = {"episodes": len(hooks), "outcomes": count(h.get("outcome") for h in hooks),
                     "with_related_examples": sum(1 for h in hooks if h.get("related_examples")),
                     "related_examples_received": sum(h.get("related_examples") or 0 for h in hooks),
                     "with_suggested": sum(1 for h in hooks if h.get("suggested")),
                     "with_procedures": sum(1 for h in hooks if h.get("procedures")),
                     "empty_context": sum(1 for c in chars if not c),
                     "context_chars_mean": round(sum(chars) / len(chars)),
                     "context_chars_median": chars[len(chars) // 2], "context_chars_max": chars[-1]}
    return u


def main() -> None:
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    tids = {t["problem_id"] for t in transfer}
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in load_attempts() if not r.get("call_error")}
    eps = {}
    for line in (demo_env.RUNS / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if not e.get("error") or e.get("steps"):
            eps[(e["problem_id"], e["model"], e["arm"])] = e
    gold = lambda m, arm, a=att: {pid: r["gold_pass"] for (pid, mm, aa), r in a.items() if mm == m and aa == arm}
    pt = lambda ts, models=OPEN_MODELS: [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in ts for m in models]
    pg = lambda arm, models=OPEN_MODELS, a=att: {f"{pid}|{m}": v for m in models for pid, v in gold(m, arm, a).items()}

    rep: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip(),
                 "n_attempts": {arm: {m: sum(1 for k in att if k[2] == arm and k[1] == m) for m in OPEN_MODELS}
                                for arm in ARMS}}
    p = paired(pt(transfer), pg("AG5"), pg("KH"))
    rep["primary_KH_minus_AG5_transfer"] = p
    rep["verdict"] = "CONFIRMED" if p.get("n") and p["ci95"][0] > 0 and p["mcnemar_p"] < 0.05 else "NOT CONFIRMED"

    rep["KP5_minus_AG5_transfer"] = paired(pt(transfer), pg("AG5"), pg("KP5"))
    rep["KH_minus_KP5_transfer"] = paired(pt(transfer), pg("KP5"), pg("KH"))
    for x, y in (("AG5", "KH"), ("AG5", "KP5"), ("KP5", "KH")):
        per = {m: paired(pt(transfer, [m]), pg(x, [m]), pg(y, [m])) for m in OPEN_MODELS}
        adj = holm({m: v["mcnemar_p"] for m, v in per.items() if v.get("n")})
        rep[f"per_model_{y}_minus_{x}"] = {m: {**v, "holm_p": adj.get(m)} for m, v in per.items()}
    rep["controls_KH_minus_AG5"] = paired(pt(control), pg("AG5"), pg("KH"))
    rep["controls_KP5_minus_AG5"] = paired(pt(control), pg("AG5"), pg("KP5"))
    for arm in ("KH", "KP5"):
        lost = [k for k in pg(arm) if k in pg("AG5") and pg("AG5")[k] and not pg(arm)[k]]
        rep[f"{arm}_lost_vs_AG5_all_tasks"] = {"n": len(lost), "which": sorted(lost)}

    rep["usage"] = {arm: {m: usage([e for (pid, mm, a), e in eps.items() if mm == m and a == arm])
                          for m in OPEN_MODELS} for arm in ("KP5", "KH")}
    # descriptive: where the hook's lookup carried code (a procedure, a suggestion or related examples)
    had = {f"{pid}|{m}" for (pid, m, a), e in eps.items() if a == "KH" and e.get("hook")
           and (e["hook"].get("procedures") or e["hook"].get("suggested") or e["hook"].get("related_examples"))}
    rep["KH_minus_AG5_transfer_where_hook_delivered_knowledge_descriptive"] = paired(
        [t for t in pt(transfer) if t["problem_id"] in had], pg("AG5"), pg("KH"))
    rep["KH_minus_AG5_transfer_where_hook_delivered_nothing_descriptive"] = paired(
        [t for t in pt(transfer) if t["problem_id"] not in had], pg("AG5"), pg("KH"))

    # claims were surveyed once in round 4 and reused (runs4/claims_*.md): no survey cost is added here
    cost = {}
    for arm in ARMS:
        row = {}
        for m in OPEN_MODELS:
            rs = [r for (pid, mm, a), r in att.items() if mm == m and a == arm]
            toks = sum(r["tokens_in"] + r["tokens_out"] for r in rs)
            usd = sum(r["cost_usd"] for r in rs)
            solved = sum(r["gold_pass"] for r in rs)
            row[m] = {"attempts": len(rs), "solved": solved, "tokens": toks, "usd": round(usd, 5),
                      "tokens_per_solved": round(toks / solved) if solved else None,
                      "usd_per_solved": round(usd / solved, 6) if solved else None}
        cost[arm] = row
    rep["cost"] = cost

    # cross-round, descriptive only (different days, same tasks/models/agent)
    r4 = [json.loads(line) for line in (demo_env.HERE / "runs4" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()]
    att4 = {(r["problem_id"], r["model"], r["arm"]): r for r in r4 if not r.get("call_error")}
    rate = lambda g: {"n": len(g), "rate": round(sum(g.values()) / max(len(g), 1), 3)}
    ctx = {}
    for arm in ARMS:
        ctx[f"round5_{arm}"] = rate({k: v for k, v in pg(arm).items() if k.split("|")[0] in tids})
    for arm in ("AG", "KN", "KP"):
        ctx[f"round4_{arm}"] = rate({k: v for k, v in pg(arm, a=att4).items() if k.split("|")[0] in tids})
    rep["context_transfer_rates"] = ctx
    rep["cross_round_descriptive"] = {
        "KH_r5_minus_KN_r4": paired(pt(transfer), pg("KN", a=att4), pg("KH")),
        "KP5_r5_minus_KP_r4": paired(pt(transfer), pg("KP", a=att4), pg("KP5")),
        "AG5_r5_minus_AG_r4_noise": paired(pt(transfer), pg("AG", a=att4), pg("AG5")),
    }

    (demo_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
