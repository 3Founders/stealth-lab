"""Round 7's preregistered analysis (PREREGISTRATION_7.md). Primary: Sonnet KH7 - AG7 on transfer tasks.

    python analyze_r7.py      # writes runs7/report.json
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs7"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"

import demo_env  # noqa: E402

import json  # noqa: E402

from analyze import paired  # noqa: E402
from analyze_r5 import count  # noqa: E402
from common import load_attempts, test_items  # noqa: E402

MODEL = "claude-sonnet-5"


def jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def main() -> None:
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    tids = {t["problem_id"] for t in transfer}
    att = {(r["problem_id"], r["arm"]): r for r in load_attempts() if r["model"] == MODEL and not r.get("call_error")}
    g = lambda arm: {pid: r["gold_pass"] for (pid, a), r in att.items() if a == arm}

    rep: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip(),
                 "n_attempts": {arm: sum(1 for k in att if k[1] == arm) for arm in ("AG7", "KH7")}}
    p = paired(transfer, g("AG7"), g("KH7"))
    rep["primary_KH7_minus_AG7_transfer"] = p
    rep["verdict"] = "CONFIRMED" if p.get("n") and p["ci95"][0] > 0 and p["mcnemar_p"] < 0.05 else "NOT CONFIRMED"
    rep["controls_KH7_minus_AG7"] = paired(control, g("AG7"), g("KH7"))
    lost = sorted(pid for pid, v in g("AG7").items() if v and pid in g("KH7") and not g("KH7")[pid])
    rep["KH7_lost_vs_AG7_all_tasks"] = {"n": len(lost), "which": lost}

    hooks = json.loads((demo_env.RUNS / "sonnet" / "hooks.json").read_text(encoding="utf-8"))
    delivered = {pid for pid, h in hooks.items() if h["text"]}
    rep["KH7_minus_AG7_transfer_where_hook_delivered_knowledge_descriptive"] = paired(
        [t for t in transfer if t["problem_id"] in delivered], g("AG7"), g("KH7"))
    rep["KH7_minus_AG7_transfer_where_hook_delivered_nothing_descriptive"] = paired(
        [t for t in transfer if t["problem_id"] not in delivered], g("AG7"), g("KH7"))
    s = [h["summary"] for h in hooks.values()]
    rep["hook_content"] = {"lookups": len(s), "outcomes": count(x["outcome"] for x in s),
                           "with_related_examples": sum(1 for x in s if x["related_examples"]),
                           "with_suggested": sum(1 for x in s if x["suggested"]),
                           "with_procedures": sum(1 for x in s if x["procedures"]),
                           "empty_context": sum(1 for x in s if not x["context_chars"]),
                           "context_chars_mean": round(sum(x["context_chars"] for x in s) / max(len(s), 1))}

    calls = jsonl(demo_env.RUNS / "sonnet_kel_calls.jsonl")
    by_task: dict = {}
    for c in calls:
        by_task.setdefault(os.path.basename(c["ws"]), []).append(c)
    fw = [c for c in calls if c["tool"] == "find_ways"]
    rep["KH7_own_kel_calls"] = {"episodes_calling_find_ways": sum(1 for cs in by_task.values()
                                                                  if any(c["tool"] == "find_ways" for c in cs)),
                                "find_ways_calls": len(fw), "outcomes": count(c.get("outcome") for c in fw),
                                "read_procedure_claims_calls": sum(1 for c in calls if c["tool"] == "read_procedure_claims")}

    cost = {}
    for arm in ("AG7", "KH7"):
        rs = [r for (pid, a), r in att.items() if a == arm]
        toks = sum(r["tokens_in"] + r["tokens_out"] for r in rs)
        solved = sum(r["gold_pass"] for r in rs)
        cost[arm] = {"attempts": len(rs), "solved": solved, "tokens_approx": toks,
                     "tokens_estimated_share": round(sum(1 for r in rs if r.get("tokens_estimated")) / max(len(rs), 1), 2),
                     "tokens_per_solved": round(toks / solved) if solved else None}
    rep["cost"] = cost

    r4 = {(r["problem_id"], r["arm"]): r["gold_pass"] for r in jsonl(demo_env.HERE / "runs4" / "attempts.jsonl")
          if r["model"] == MODEL and not r.get("call_error")}
    rate = lambda d: {"n": len(d), "rate": round(sum(d.values()) / max(len(d), 1), 3)}
    ctx = {f"round7_{arm}": rate({k: v for k, v in g(arm).items() if k in tids}) for arm in ("AG7", "KH7")}
    for arm in ("AG", "KP"):
        ctx[f"round4_sonnet_{arm}"] = rate({pid: v for (pid, a), v in r4.items() if a == arm and pid in tids})
    rep["context_transfer_rates"] = ctx
    rep["cross_round_descriptive"] = {
        "AG7_minus_round4_AG_noise": paired(transfer, {p: v for (p, a), v in r4.items() if a == "AG"}, g("AG7")),
        "KH7_minus_round4_KP": paired(transfer, {p: v for (p, a), v in r4.items() if a == "KP"}, g("KH7")),
    }

    (demo_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
