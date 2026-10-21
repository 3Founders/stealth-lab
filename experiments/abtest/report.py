"""Summarise A-vs-C runs: per suite and overall -- solved, cost, saving, latency percentiles, who delivered.

    python experiments/abtest/report.py phase1 [more runs...]       # tasks finished in BOTH arms only
    python experiments/abtest/report.py toy1 --curve                # a long-horizon series: the learning curve per arm

Latency is each session's wall time (the runner's `seconds`): for C it includes the hooks -- the lookup, the
test-first call, the cheap tries and, when they fail, the session's own model.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import os

NL = chr(10)

# ABTEST_RESULTS_DIR=experiments/abtest/results_2026-10-11 reads the committed snapshot instead of .local/results
RESULTS = Path(os.environ.get("ABTEST_RESULTS_DIR") or Path(__file__).resolve().parent / ".local" / "results")


def pct(xs: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(-(-p * len(s) // 100)) - 1))
    return s[k]


def load(runs: list[str]) -> dict[tuple[str, str], dict]:
    rows = {}
    for run in runs:
        for line in (RESULTS / f"{run}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "done":
                    rows[(r["task_id"], r["arm"])] = r
    return rows


def summarise(rows: dict) -> str:
    tasks = sorted({t for t, a in rows if (t, "A") in rows and (t, "C") in rows})
    groups = collections.defaultdict(list)
    for t in tasks:
        groups[rows[(t, "A")]["suite"]].append(t)
    groups["ALL"] = tasks
    out = []
    head = (f"{'suite':8} {'n':>4} | {'A solved':>8} {'A $':>7} | {'C solved':>8} {'C $':>7} {'saving':>7} | "
            f"{'A p50/p95/p99 s':>17} | {'C p50/p95/p99 s':>17} | cheap delivered | not dispatched")
    out.append(head)
    out.append("-" * len(head))
    for suite in sorted(groups, key=lambda s: (s == "ALL", s)):
        ts = groups[suite]
        a = [rows[(t, "A")] for t in ts]
        c = [rows[(t, "C")] for t in ts]
        cost = lambda rs: sum(float(r.get("total_cost_usd") or 0) for r in rs)  # noqa: E731
        solved = lambda rs: sum(bool(r.get("resolved")) for r in rs)  # noqa: E731
        secs = lambda rs: [float(r.get("seconds") or 0) for r in rs]  # noqa: E731
        ca, cc = cost(a), cost(c)          # saving: what C costs less than A, as a share of A (negative: C costs more)
        # a cheap answer delivered: a turn the cheap models handled (multi-turn: the session may still run later turns)
        cheap = sum(1 for r in c if any(t.get("dispatched") for t in r.get("turn_log") or [])
                    or (any(x.get("state") == "verified" and x.get("role", "attempt") == "attempt"
                            and "claude" not in x.get("model", "") for x in r.get("attempts") or [])
                        and r.get("turns", 0) == 0))
        skipped = sum(1 for r in c if not (r.get("attempts") or []))       # routing sent it straight to the session model
        lat = lambda xs: f"{pct(xs, 50):5.0f}/{pct(xs, 95):5.0f}/{pct(xs, 99):5.0f}"  # noqa: E731
        out.append(f"{suite:8} {len(ts):>4} | {solved(a):>8} {ca:>7.2f} | {solved(c):>8} {cc:>7.2f} "
                   f"{(ca - cc) / ca * 100 if ca else 0:>6.0f}% | {lat(secs(a)):>17} | {lat(secs(c)):>17} | "
                   f"{cheap:>7}/{len(ts):<7} | {skipped}/{len(ts)}")
    # the turns the cheap models handled in C: changes ("Done by") and answers ("Answered by"), of all of C's turns
    turns = [t for t in tasks for t in (rows[(t, "C")].get("turn_log") or [])]
    if turns:
        changes = sum(1 for t in turns if t.get("dispatched") and not t.get("answered"))
        answers = sum(1 for t in turns if t.get("answered"))
        out.append(f"\nC turns done by cheap models: {changes + answers} of {len(turns)} ({changes} changes, {answers} answers)")
        grades = [(t.get("answer_grade") or {}).get("grade") for t in turns if t.get("answered")]
        if grades:
            out.append(f"C cheap answers, graded against the merged fix: {grades.count('correct')} correct, "
                       f"{grades.count('incorrect')} incorrect, {grades.count('unclear')} unclear, {grades.count(None)} not graded")
    wrong = [(t, rows[(t, "C")].get("suite")) for t in tasks if not rows[(t, "C")].get("resolved")]
    both = [t for t, _ in wrong if not rows[(t, "A")].get("resolved")]
    out.append(f"\nC unsolved: {len(wrong)} (of which A also unsolved: {len(both)}): {[t for t, _ in wrong]}")
    return "\n".join(out)


def curve(rows: dict) -> str:
    """The learning curve of a long-horizon series (runner.py --series): per arm (A, C, and C0 = C with .stealth wiped each
    task) and per position in the series, solved and cost. What C has over C0 is what a persistent .stealth is worth; the
    late positions are where it should show. Only tasks done in every arm present are counted, so the arms compare."""
    arms = sorted({a for _, a in rows})
    by = {(r["position"], a): r for (_, a), r in rows.items() if "position" in r}
    positions = sorted({p for p, _ in by})
    full = [p for p in positions if all((p, a) in by for a in arms)]
    if not full:
        return "no position is finished in every arm yet"
    cost = lambda r: float(r.get("total_cost_usd") or 0)  # noqa: E731
    head = f"{'position':>8} {'task':<26}" + "".join(f" | {a + ' solved':>9} {a + ' $':>7}" for a in arms)
    out = [head, "-" * len(head)]
    for p in full:
        task = by[(p, arms[0])]["task_id"]
        out.append(f"{p:>8} {task:<26}" + "".join(f" | {'yes' if by[(p, a)].get('resolved') else 'no':>9} {cost(by[(p, a)]):>7.3f}" for a in arms))
    tot = {a: (sum(bool(by[(p, a)].get("resolved")) for p in full), sum(cost(by[(p, a)]) for p in full)) for a in arms}
    out.append("-" * len(head))
    out.append(f"{'all':>8} {'':<26}" + "".join(f" | {tot[a][0]:>3}/{len(full):<5} {tot[a][1]:>7.3f}" for a in arms))
    later = [p for p in full if p > full[0]]
    if later and "A" in arms:
        base = sum(cost(by[(p, "A")]) for p in later)
        for a in arms:
            if a != "A" and base:
                out.append(f"positions after the first, {a} vs A: {(sum(cost(by[(p, a)]) for p in later) - base) / base * 100:+.0f}% cost, "
                           f"{sum(bool(by[(p, a)].get('resolved')) for p in later)}/{len(later)} solved "
                           f"(A {sum(bool(by[(p, 'A')].get('resolved')) for p in later)}/{len(later)})")
    return NL.join(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = load(args or ["phase1"])
    print(curve(rows) if "--curve" in sys.argv else summarise(rows))
