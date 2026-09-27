"""Round 6's preregistered analysis (PREREGISTRATION_6.md). Primary: pooled KH (round 5) - KH0 on transfer tasks.

    python analyze_r6.py      # writes runs6/report.json
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs6"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"

import demo_env  # noqa: E402

import json  # noqa: E402

from analyze import holm, paired  # noqa: E402
from analyze_r5 import count  # noqa: E402
from common import load_attempts, test_items  # noqa: E402
from models import OPEN_MODELS  # noqa: E402

ARMS6 = ("KH0", "KHnR", "KHnS", "KHr")


def jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def main() -> None:
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in load_attempts() if not r.get("call_error")}
    r5 = {(r["problem_id"], r["model"], r["arm"]): r for r in jsonl(demo_env.HERE / "runs5" / "attempts.jsonl")
          if not r.get("call_error") and r["arm"] in ("KH", "AG5")}
    att.update(r5)   # round-5 KH and AG5, unchanged, as comparators
    eps = {}
    for path in (demo_env.HERE / "runs5" / "episodes.jsonl", demo_env.RUNS / "episodes.jsonl"):
        for e in jsonl(path):
            if e.get("arm") in ("KH", *ARMS6) and (not e.get("error") or e.get("steps")):
                eps[(e["problem_id"], e["model"], e["arm"])] = e
    gold = lambda m, arm: {pid: r["gold_pass"] for (pid, mm, a), r in att.items() if mm == m and a == arm}
    pt = lambda ts, models=OPEN_MODELS: [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in ts for m in models]
    pg = lambda arm, models=OPEN_MODELS: {f"{pid}|{m}": v for m in models for pid, v in gold(m, arm).items()}

    rep: dict = {"design_sha256": (demo_env.RUNS / "design.sha256").read_text().strip(),
                 "n_attempts": {arm: {m: sum(1 for k in att if k[2] == arm and k[1] == m) for m in OPEN_MODELS}
                                for arm in ("KH", *ARMS6)}}
    replay = jsonl(demo_env.RUNS / "hook_replay.jsonl")
    rep["hook_replay"] = {"n": len(replay), "identical": sum(all(r["same"].values()) for r in replay)}

    p = paired(pt(transfer), pg("KH0"), pg("KH"))
    rep["primary_KH_minus_KH0_transfer"] = p
    rep["verdict"] = "CONFIRMED" if p.get("n") and p["ci95"][0] > 0 and p["mcnemar_p"] < 0.05 else "NOT CONFIRMED"

    pairs = (("KHnR", "KH"), ("KHnS", "KH"), ("KH0", "KHnR"), ("KH0", "KHnS"), ("AG5", "KH0"))
    for x, y in pairs:   # y - x
        rep[f"{y}_minus_{x}_transfer"] = paired(pt(transfer), pg(x), pg(y))
    for x, y in (("KH0", "KH"), *pairs):
        per = {m: paired(pt(transfer, [m]), pg(x, [m]), pg(y, [m])) for m in OPEN_MODELS}
        adj = holm({m: v["mcnemar_p"] for m, v in per.items() if v.get("n")})
        rep[f"per_model_{y}_minus_{x}"] = {m: {**v, "holm_p": adj.get(m)} for m, v in per.items()}
    # KHr: round-5 KH re-run -- replication of round 5's primary, and outcome noise from Kel alone
    rep["replication_KHr_minus_AG5_transfer"] = paired(pt(transfer), pg("AG5"), pg("KHr"))
    noise = paired(pt(transfer), pg("KH"), pg("KHr"))
    rep["noise_KHr_minus_KH_transfer"] = {**noise, "flip_rate": round((noise.get("gained", 0) + noise.get("lost", 0))
                                                                      / max(noise.get("n", 0), 1), 3)}
    rep["controls_KH_minus_KH0"] = paired(pt(control), pg("KH0"), pg("KH"))
    rep["controls_KH0_minus_AG5"] = paired(pt(control), pg("AG5"), pg("KH0"))

    # descriptive: where the hook's delivery differed between KH and KH0 (with a deterministic agent, only these
    # pairs can differ in outcome beyond Kel's own run-to-run variation)
    tids = {t["problem_id"] for t in transfer}
    def hook_of(key):
        return (eps.get(key) or {}).get("hook") or {}
    differ = {f"{pid}|{m}" for pid in tids for m in OPEN_MODELS
              if hook_of((pid, m, "KH")).get("context_chars") != hook_of((pid, m, "KH0")).get("context_chars")}
    rep["KH_minus_KH0_where_hook_text_differed_descriptive"] = paired(
        [t for t in pt(transfer) if t["problem_id"] in differ], pg("KH0"), pg("KH"))
    rep["KH_minus_KH0_where_hook_text_same_descriptive"] = paired(
        [t for t in pt(transfer) if t["problem_id"] not in differ], pg("KH0"), pg("KH"))

    hooks = {}
    for arm in ("KH", *ARMS6):
        hs = [e["hook"] for (pid, m, a), e in eps.items() if a == arm and e.get("hook")]
        chars = sorted(h.get("context_chars") or 0 for h in hs)
        hooks[arm] = {"episodes": len(hs), "outcomes": count(h.get("outcome") for h in hs),
                      "with_related_examples": sum(1 for h in hs if h.get("related_examples")),
                      "with_suggested": sum(1 for h in hs if h.get("suggested")),
                      "with_procedures": sum(1 for h in hs if h.get("procedures")),
                      "empty_context": sum(1 for c in chars if not c),
                      "context_chars_mean": round(sum(chars) / max(len(chars), 1)),
                      "context_chars_max": chars[-1] if chars else 0}
    rep["hook_content"] = hooks

    cost = {}
    for arm in ("AG5", "KH", *ARMS6):
        row = {}
        for m in OPEN_MODELS:
            rs = [r for (pid, mm, a), r in att.items() if mm == m and a == arm]
            toks = sum(r["tokens_in"] + r["tokens_out"] for r in rs)
            solved = sum(r["gold_pass"] for r in rs)
            row[m] = {"attempts": len(rs), "solved": solved, "tokens": toks,
                      "tokens_per_solved": round(toks / solved) if solved else None,
                      "usd_per_solved": round(sum(r["cost_usd"] for r in rs) / solved, 6) if solved else None}
        cost[arm] = row
    rep["cost"] = cost
    rep["transfer_rates"] = {arm: {"n": len(v), "rate": round(sum(v.values()) / max(len(v), 1), 3)}
                             for arm in ("AG5", "KH", *ARMS6)
                             for v in [{k: g for k, g in pg(arm).items() if k.split("|")[0] in tids}]}

    (demo_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
