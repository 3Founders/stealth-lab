"""
Independent verification script for the model-decides tier's live sweep
(experiments/harness/fixtures/model_decides/, board Lane MEASURE fifth wave,
CLAUDE.md Task 2). Same discipline as run1_verify.py: pure stdlib,
recomputes classification straight from the raw sweep JSONL + procedures.json
ground truth, does NOT import model_decides.py (that's the module under
verification) or scoring.py. Cross-checks the exact McNemar test against
mcnemar_power.py, which was itself independently re-derived and confirmed
correct during RUN #1 verification, so re-deriving it a third time here
would just be re-proving stdlib math.comb — the module isn't what's in
question, the DATA and the CLASSIFICATION LOGIC are.

Reads raw episodes from wherever --results points (defaults to a path this
lane does not own; pass the real location explicitly). model_decides_report.json
(committed) is read only for cross-checking, never trusted as ground truth.

Usage:
    python model_decides_verify.py --results <path to model_decides_results.jsonl> \
        --fixtures-dir <path to fixtures/model_decides> \
        [--spend <path to model_decides_spend.jsonl>] \
        [--report <path to model_decides_report.json>]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def two_sided_exact_p(k_le: int, n: int) -> float:
    k = min(k_le, n - k_le)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def task_role(task: dict) -> str:
    has_stale = bool(task.get("stale_offer"))
    has_app = bool(task.get("applicable_procedure"))
    if has_stale and not has_app:
        return "trap"
    if has_app and not has_stale:
        return "control"
    return "unknown"


def offered_pid(task: dict) -> str | None:
    return task.get("stale_offer") or task.get("applicable_procedure")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--fixtures-dir", required=True)
    ap.add_argument("--spend", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    fixtures_dir = Path(args.fixtures_dir)
    tasks_data = json.loads((fixtures_dir / "tasks.json").read_text(encoding="utf-8"))
    tasks = {t["task_id"]: t for t in tasks_data["tasks"]}
    procs_data = json.loads((fixtures_dir / "procedures.json").read_text(encoding="utf-8"))
    procs_by_id = {p["procedure_id"]: p for p in procs_data["procedures"]}

    rows = load_jsonl(Path(args.results))
    print(f"raw episode rows: {len(rows)}")

    trap_pairs = []   # (B_pass, C_pass, task_id)
    control_false_refusals = []  # (task_id, false_refusal_bool)
    invalid = []
    journal_gate_hits = []  # (task_id, tool, verdict) where verdict False under bypass — should never happen
    refusal_reasons = []

    for r in rows:
        tid = r["task_id"]
        task = tasks.get(tid)
        if task is None:
            continue
        role = task_role(task)
        b, c = r.get("B"), r.get("C")
        for arm_name, ep in (("A", r.get("A")), ("B", b), ("C", c)):
            if ep is not None and not ep.get("valid", True):
                invalid.append((tid, arm_name, ep.get("invalid_reason")))

        pid = offered_pid(task)
        is_stale = bool((procs_by_id.get(pid) or {}).get("stale"))

        journal = r.get("C_journal") or []
        for e in journal:
            if e.get("tool") == "check_applicability" and e.get("verdict") is False:
                journal_gate_hits.append((tid, e))
            if e.get("tool") == "record_refusal":
                refusal_reasons.append((tid, e.get("procedure_id"), e.get("reason")))

        if role == "trap":
            b_pass = c_pass = None
            if b is not None and b.get("valid", True):
                b_pass = not bool(b.get("reuse_caused_failure"))
            if c is not None and c.get("valid", True):
                refused = c.get("refused_procedure_ids") or []
                c_pass = (pid in refused) and is_stale
            if b_pass is not None and c_pass is not None:
                trap_pairs.append((b_pass, c_pass, tid))
        elif role == "control":
            if c is not None and c.get("valid", True):
                refused = c.get("refused_procedure_ids") or []
                fr = (pid in refused) and (not is_stale)
                control_false_refusals.append((tid, fr))

    print(f"\ninvalid episodes: {invalid or 'none'}")

    n_trap = len(trap_pairs)
    b_only = sum(1 for bp, cp, _ in trap_pairs if bp and not cp)
    c_only = sum(1 for bp, cp, _ in trap_pairs if cp and not bp)
    concordant = n_trap - b_only - c_only
    n_disc = b_only + c_only
    print(f"\n=== SENSITIVITY (trap tasks, n={n_trap} valid-both pairs) ===")
    print(f"concordant: {concordant}  discordant: {n_disc} (B_only={b_only}, C_only={c_only})")
    if n_disc:
        k = min(b_only, c_only)
        p = two_sided_exact_p(k, n_disc)
        q = max(b_only, c_only) / n_disc
        print(f"exact two-sided McNemar p = {p:.8f}  (q_observed={q:.4f})")
    else:
        print("zero discordant pairs — no test")

    print("\nper-task trap pass/fail (B, C):")
    for bp, cp, tid in trap_pairs:
        marker = " <-- DISCORDANT" if bp != cp else ""
        print(f"  {tid}: B={bp} C={cp}{marker}")

    n_ctrl = len(control_false_refusals)
    n_fr = sum(1 for _, fr in control_false_refusals if fr)
    print(f"\n=== SPECIFICITY (control tasks, n={n_ctrl}) ===")
    print(f"false refusals: {n_fr}/{n_ctrl} = {(n_fr/n_ctrl if n_ctrl else float('nan')):.4f}")

    print(f"\n=== JOURNAL CHECK (arm C) ===")
    print(f"check_applicability verdict=False hits under bypass (should be ZERO): "
          f"{len(journal_gate_hits)}")
    if journal_gate_hits:
        for tid, e in journal_gate_hits:
            print(f"  ANOMALY {tid}: {e}")
    print(f"record_refusal reason strings ({len(refusal_reasons)} total):")
    mechanical_gate_phrase = "assumptions no longer hold (gate)"
    n_mechanical = 0
    for tid, pid_r, reason in refusal_reasons:
        flag = ""
        if reason == mechanical_gate_phrase:
            flag = "  <-- MECHANICAL GATE STRING (should be impossible under bypass)"
            n_mechanical += 1
        print(f"  {tid} refused {pid_r}: {reason!r}{flag}")
    print(f"\nmechanical-gate-phrase refusals: {n_mechanical} (expect 0)")

    if args.spend:
        spend_rows = load_jsonl(Path(args.spend))
        from collections import Counter
        by_model_status = Counter((r.get("model"), r.get("status")) for r in spend_rows)
        print(f"\n=== SPEND / MODEL ATTRIBUTION ({len(spend_rows)} attempt rows) ===")
        for k, v in sorted(by_model_status.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")
        billed = [r for r in spend_rows if r.get("status") == 200]
        total_cost = sum(r.get("cost_usd", 0) or 0 for r in billed)
        print(f"  billed attempts: {len(billed)}  total cost: ${total_cost:.6f}")
        n_429 = sum(1 for r in spend_rows if r.get("status") == 429)
        n_404 = sum(1 for r in spend_rows if r.get("status") == 404)
        print(f"  429 count: {n_429}  404 count: {n_404}")

    if args.report:
        shipped = json.loads(Path(args.report).read_text(encoding="utf-8"))
        print("\n=== CROSS-CHECK vs committed model_decides_report.json ===")
        shipped_disc = shipped.get("sensitivity_discordant", {})
        print(f"  shipped B_only={shipped_disc.get('B_only')} C_only={shipped_disc.get('C_only')}"
              f"  vs recomputed B_only={b_only} C_only={c_only}"
              f"  -> {'MATCH' if (shipped_disc.get('B_only'), shipped_disc.get('C_only')) == (b_only, c_only) else 'MISMATCH'}")
        print(f"  shipped n_false_refusal={shipped.get('n_false_refusal')}"
              f"  vs recomputed={n_fr}"
              f"  -> {'MATCH' if shipped.get('n_false_refusal') == n_fr else 'MISMATCH'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
