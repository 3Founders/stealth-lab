"""Round 4's preregistered analysis (PREREGISTRATION_4.md). Primary: pooled KP - AG on transfer tasks.

    python analyze_r4.py      # writes runs4/report.json
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs4"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"

import demo_env  # noqa: E402

import json  # noqa: E402

from analyze import holm, paired  # noqa: E402
from common import load_attempts, test_items  # noqa: E402
from models import OPEN_MODELS, cost_usd  # noqa: E402

ARMS = ("AG", "KN", "KP")


def main() -> None:
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in load_attempts() if not r.get("call_error")}
    eps = {}
    for line in (demo_env.RUNS / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if not e.get("error") or e.get("steps"):
            eps[(e["problem_id"], e["model"], e["arm"])] = e
    gold = lambda m, arm: {pid: r["gold_pass"] for (pid, mm, a), r in att.items() if mm == m and a == arm}
    pt = lambda ts, models=OPEN_MODELS: [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in ts for m in models]
    pg = lambda arm, models=OPEN_MODELS: {f"{pid}|{m}": v for m in models for pid, v in gold(m, arm).items()}

    rep: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip(),
                 "n_attempts": {arm: sum(1 for k in att if k[2] == arm) for arm in ARMS}}
    p = paired(pt(transfer), pg("AG"), pg("KP"))
    rep["primary_KP_minus_AG_transfer"] = p
    rep["verdict"] = "CONFIRMED" if p.get("n") and p["ci95"][0] > 0 and p["mcnemar_p"] < 0.05 else "NOT CONFIRMED"

    per = {m: paired(pt(transfer, [m]), pg("AG", [m]), pg("KP", [m])) for m in OPEN_MODELS}
    adj = holm({m: v["mcnemar_p"] for m, v in per.items() if v.get("n")})
    rep["per_model_KP_minus_AG"] = {m: {**v, "holm_p": adj.get(m)} for m, v in per.items()}
    rep["KN_minus_AG_transfer"] = paired(pt(transfer), pg("AG"), pg("KN"))
    rep["KP_minus_KN_transfer"] = paired(pt(transfer), pg("KN"), pg("KP"))
    rep["controls_KP_minus_AG"] = paired(pt(control), pg("AG"), pg("KP"))
    lost = [k for k in pg("KP") if k in pg("AG") and pg("AG")[k] and not pg("KP")[k]]
    rep["KP_lost_vs_AG_all_tasks"] = {"n": len(lost), "which": sorted(lost)}

    usage = {}
    for m in OPEN_MODELS:
        kp = [e for (pid, mm, a), e in eps.items() if mm == m and a == "KP"]
        fw = [[c for c in (e.get("kel_calls") or []) if c["tool"] == "find_ways"] for e in kp]
        outcomes: dict = {}
        for calls in fw:
            for c in calls:
                outcomes[c.get("outcome")] = outcomes.get(c.get("outcome"), 0) + 1
        usage[m] = {"episodes": len(kp), "called_find_ways": sum(bool(c) for c in fw),
                    "find_ways_outcomes": outcomes,
                    "got_verified_solution": sum(any(c.get("verified_solution") for c in calls) for calls in fw),
                    "mean_kel_calls": round(sum(len(e.get("kel_calls") or []) for e in kp) / max(len(kp), 1), 2),
                    "mean_steps": round(sum(e.get("steps") or 0 for e in kp) / max(len(kp), 1), 2)}
    rep["KP_usage"] = usage
    called = {f"{pid}|{m}" for (pid, m, a), e in eps.items() if a == "KP"
              and any(c["tool"] == "find_ways" for c in (e.get("kel_calls") or []))}
    rep["KP_minus_AG_transfer_where_KP_called_find_ways_descriptive"] = paired(
        [t for t in pt(transfer) if t["problem_id"] in called], pg("AG"), pg("KP"))

    survey = json.loads((demo_env.RUNS / "survey.json").read_text(encoding="utf-8"))
    cost = {}
    for arm in ARMS:
        row = {}
        for m in OPEN_MODELS:
            rs = [r for (pid, mm, a), r in att.items() if mm == m and a == arm]
            toks = sum(r["tokens_in"] + r["tokens_out"] for r in rs)
            usd = sum(r["cost_usd"] for r in rs)
            if arm == "KP":
                for s in survey.get(m, []):
                    u = s["usage"]
                    toks += u["prompt_tokens"] + u["completion_tokens"]
                    usd += cost_usd(m, u["prompt_tokens"], u["completion_tokens"])
            solved = sum(r["gold_pass"] for r in rs)
            row[m] = {"attempts": len(rs), "solved": solved, "tokens": toks, "usd": round(usd, 5),
                      "tokens_per_solved": round(toks / solved) if solved else None,
                      "usd_per_solved": round(usd / solved, 6) if solved else None}
        cost[arm] = row
    rep["cost"] = cost

    r3 = [json.loads(line) for line in (demo_env.HERE / "runs3" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()]
    r3g = {(r["problem_id"], r["model"], r["arm"]): r["gold_pass"] for r in r3 if not r.get("call_error")}
    ctx = {}
    for arm in ("A", "K"):
        vals = [r3g[(t["problem_id"], m, arm)] for t in transfer for m in OPEN_MODELS if (t["problem_id"], m, arm) in r3g]
        ctx[f"round3_single_shot_{arm}"] = {"n": len(vals), "rate": round(sum(vals) / max(len(vals), 1), 3)}
    for arm in ARMS:
        vals = [v for k, v in pg(arm).items() if k.split("|")[0] in {t["problem_id"] for t in transfer}]
        ctx[f"round4_agent_{arm}"] = {"n": len(vals), "rate": round(sum(vals) / max(len(vals), 1), 3)}
    rep["context_transfer_rates"] = ctx

    # Deviation 1: Sonnet through Claude Code subagents, AG and KP only -- reported separately, never pooled.
    son = ["claude-sonnet-5"]
    sonnet = {"KP_minus_AG_transfer": paired(pt(transfer, son), pg("AG", son), pg("KP", son)),
              "KP_minus_AG_controls": paired(pt(control, son), pg("AG", son), pg("KP", son))}
    calls_path = demo_env.RUNS / "sonnet_kel_calls.jsonl"
    calls = [json.loads(l) for l in calls_path.read_text(encoding="utf-8").splitlines() if l.strip()] if calls_path.exists() else []
    by_task: dict = {}
    for c in calls:
        by_task.setdefault(os.path.basename(c["ws"]), []).append(c)
    fw = {pid: [c for c in cs if c["tool"] == "find_ways"] for pid, cs in by_task.items()}
    outcomes: dict = {}
    for cs in fw.values():
        for c in cs:
            outcomes[c.get("outcome")] = outcomes.get(c.get("outcome"), 0) + 1
    sonnet["KP_usage"] = {"episodes": 89, "called_find_ways": sum(bool(v) for v in fw.values()),
                          "find_ways_outcomes": outcomes,
                          "got_verified_solution": sum(any(c.get("verified_solution") for c in v) for v in fw.values()),
                          "mean_kel_calls": round(len(calls) / 89, 2)}
    called_s = {f"{pid}|claude-sonnet-5" for pid, v in fw.items() if v}
    sonnet["KP_minus_AG_transfer_where_KP_called_find_ways_descriptive"] = paired(
        [t for t in pt(transfer, son) if t["problem_id"] in called_s], pg("AG", son), pg("KP", son))
    for arm in ("AG", "KP"):
        rs = [r for (pid, mm, a), r in att.items() if mm == son[0] and a == arm]
        toks = sum(r["tokens_in"] + r["tokens_out"] for r in rs)
        solved = sum(r["gold_pass"] for r in rs)
        sonnet[f"cost_{arm}"] = {"attempts": len(rs), "solved": solved, "tokens_approx": toks,
                                 "usd_approx": round(sum(r["cost_usd"] for r in rs), 3),
                                 "tokens_per_solved": round(toks / solved) if solved else None}
    rep["sonnet_deviation_1"] = sonnet
    vals = [v for k, v in pg("AG", son).items() if k.split("|")[0] in {t["problem_id"] for t in transfer}]
    rep["context_transfer_rates"]["round4_claude_code_sonnet_AG"] = {"n": len(vals), "rate": round(sum(vals) / max(len(vals), 1), 3)}
    r3s = [r3g[(t["problem_id"], son[0], "A")] for t in transfer if (t["problem_id"], son[0], "A") in r3g]
    rep["context_transfer_rates"]["round3_single_shot_sonnet_A"] = {"n": len(r3s), "rate": round(sum(r3s) / max(len(r3s), 1), 3)}

    (demo_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
