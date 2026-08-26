"""RUN #1 independent verification (Lane RESEARCH, board item 2026-08-26).

Recomputes every headline claim from the RAW artifacts without importing the
harness scoring pipeline. Pure stdlib. Read-only over the integrator checkout
(results/spend are gitignored by the lane rule and live only there).

Frames audited for the McNemar stale-refusal claim:
  F1  shipped-scoreboard frame: tasks valid on ALL of A,B,C
  F2  pairwise B/C frame: tasks valid on BOTH B,C
  F3  all rows: invalid episode counts as non-pass / non-refusal
The board's "C 6/6 vs B 0/6 -> p~0.031" is reproducible only under a frame
that admits pdf-003's INVALID arm-B episode as a paired observation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

RESULTS = Path(r"C:\Users\user\stealth-lab\experiments\harness\real_arms_results.jsonl")
SPEND = Path(r"C:\Users\user\stealth-lab\experiments\harness\real_spend.jsonl")
FIXTURES = Path(__file__).resolve().parents[2] / "experiments/harness/fixtures/micro"
ARMS = ("A", "B", "C")

rows = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
procs = {p["procedure_id"]: p
         for p in json.loads((FIXTURES / "procedures.json").read_text(encoding="utf-8"))["procedures"]}
STALE = {pid for pid, p in procs.items() if p.get("stale")}
print(f"rows={len(rows)}  stale-ground-truth={sorted(STALE)}")


def mcnemar_two_sided_exact(b: int, c: int) -> float:
    """b = first-arm-only wins, c = second-arm-only wins.
    p = min(1, 2 * P(X <= min(b,c)) under Bin(b+c, .5)) - small-tail doubling."""
    n = b + c
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2.0 * tail)


# ---- independent per-episode classification -------------------------------
def facts(ep: dict) -> dict:
    refused_stale = [pid for pid in ep.get("refused_procedure_ids") or [] if pid in STALE]
    offered_stale = [pid for pid in ep.get("stale_offered") or [] if pid in STALE]
    reused_stale = [pid for pid in ep.get("reused_procedure_ids") or [] if pid in STALE]
    any_reuse_attempt = bool(ep.get("reused_procedure_ids") or ep.get("followed_memory_ids"))
    return {
        "valid": bool(ep.get("valid")),
        "pass": bool(ep.get("resolved")),
        "offered_stale": bool(offered_stale),
        "refused_stale": bool(refused_stale),
        "reused_stale": bool(reused_stale),
        "false_reuse": (not ep.get("resolved") and ep.get("reuse_caused_failure")
                        and any_reuse_attempt),
        "unseen": bool(ep.get("unseen_task")),
    }


F = {r["task_id"]: {a: facts(r[a]) for a in ARMS if r.get(a)} for r in rows}
task_ids = [r["task_id"] for r in rows]

# ---- headline claims -------------------------------------------------------
print("\n== per-arm resolution (three denominator frames) ==")
for label, keep in (
    ("F3 all-11-rows", lambda f: True),
    ("F1 all-arms-valid", lambda t: all(F[t].get(a, {}).get("valid") for a in ARMS)),
    ("per-arm-valid-only", None),
):
    if keep is None:
        for a in ARMS:
            ts = [t for t in task_ids if F[t].get(a) and F[t][a]["valid"]]
            passes = sum(F[t][a]["pass"] for t in ts)
            print(f"  {a}: {passes}/{len(ts)} valid-episodes")
        continue
    ts = [t for t in task_ids if keep(t)]
    line = "  " + label + ": "
    line += " | ".join(
        f"{a} {sum(F[t][a]['pass'] for t in ts if a in F[t])}/"
        f"{sum(1 for t in ts if a in F[t])}" for a in ARMS)
    print(line)

print("\n== false reuse ==")
for a in ARMS:
    fr = [t for t in task_ids if a in F[t] and F[t][a]["false_reuse"]]
    print(f"  {a}: {len(fr)} {fr}")

print("\n== stale-refusal raw facts ==")
for t in task_ids:
    parts = []
    for a in ("B", "C"):
        f = F[t].get(a)
        if not f:
            parts.append(f"{a}:ABSENT")
        else:
            tag = "OFFERED " if f["offered_stale"] else "no-offer"
            act = "REFUSED" if f["refused_stale"] else (
                "REUSED!" if f["reused_stale"] else ("invalid" if not f["valid"] else "no-refusal"))
            parts.append(f"{a}:{tag}{act}")
    print(f"  {t}: " + " | ".join(parts))

print("\n== paired stale-refusal McNemar (B vs C), per frame ==")


def frame_tasks(name):
    if name == "F1 all-arms-valid":
        return [t for t in task_ids if all(F[t].get(a, {}).get("valid") for a in ARMS)]
    if name == "F2 B-and-C-valid":
        return [t for t in task_ids if F[t].get("B", {}).get("valid") and F[t].get("C", {}).get("valid")]
    if name == "F3 all-rows(invalid counts as no-refusal)":
        return list(task_ids)
    raise KeyError(name)


for name in ("F1 all-arms-valid", "F2 B-and-C-valid",
             "F3 all-rows(invalid counts as no-refusal)"):
    ts = set(frame_tasks(name))
    b_only = c_only = 0
    contrib = []
    c_correct = b_opps = c_opps = b_refused = 0
    for t in task_ids:
        if t not in ts:
            continue
        fb, fc = F[t]["B"], F[t]["C"]
        if fb["offered_stale"]:
            b_opps += 1
            b_refused += fb["refused_stale"]
        if fc["offered_stale"]:
            c_opps += 1
            c_correct += fc["refused_stale"]
        if fb["offered_stale"] or fc["offered_stale"]:
            rb, rc = fb["refused_stale"], fc["refused_stale"]
            if rb and not rc:
                b_only += 1
                contrib.append(t + "(B)")
            if rc and not rb:
                c_only += 1
                contrib.append(t + "(C)")
    p = mcnemar_two_sided_exact(b_only, c_only)
    print(f"  {name}:")
    print(f"    marginal: B {b_refused}/{b_opps}  C {c_correct}/{c_opps}")
    print(f"    discordant: B-only={b_only} C-only={c_only} -> "
          f"exact two-sided p={p:.6f}"
          f"{'   <-- board claim' if abs(p - 0.03125) < 1e-9 else ''}")
    if contrib:
        print(f"    discordant tasks: {contrib}")

print("\n== resolution McNemar B vs C (board: 'not significant, 1-1') ==")
for name in ("F2 B-and-C-valid", "F3 all-rows(invalid counts as no-refusal)"):
    ts = set(frame_tasks(name))
    b_only = sum(1 for t in task_ids if t in ts
                 and F[t]["B"]["pass"] and not F[t]["C"]["pass"])
    c_only = sum(1 for t in task_ids if t in ts
                 and F[t]["C"]["pass"] and not F[t]["B"]["pass"])
    p = mcnemar_two_sided_exact(b_only, c_only)
    print(f"  {name}: B-only={b_only} C-only={c_only} p={p:.4f}")

print("\n== spend ledger ==")
spend = [json.loads(l) for l in SPEND.read_text(encoding="utf-8").splitlines() if l.strip()]
billed = [r for r in spend if r.get("tokens_in") or r.get("tokens_out")]
print(f"  attempts={len(spend)} billed={len(billed)} failed={len(spend)-len(billed)}")
print(f"  cost=${sum(r['cost_usd'] for r in billed):.4f}  "
      f"tokens={sum(r['tokens_in'] for r in billed):,}in/"
      f"{sum(r['tokens_out'] for r in billed):,}out")
from collections import Counter
st = Counter(("ok" if r.get("status") == 200 else str(r.get("error") or r.get("status"))) for r in spend)
print(f"  attempt-status profile={dict(st)}")
