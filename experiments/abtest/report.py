"""Summarise A-vs-C runs: per suite and overall -- solved, cost, saving, latency percentiles, who delivered.

    python experiments/abtest/report.py phase1 [more runs...]       # tasks finished in BOTH arms only

Latency is each session's wall time (the runner's `seconds`): for C it includes the hooks -- the lookup, the
test-first call, the cheap tries and, when they fail, the session's own model.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / ".local" / "results"


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
        ca, cc = cost(a), cost(c)
        cheap = sum(1 for r in c if any(x.get("state") == "verified" and x.get("role", "attempt") == "attempt"
                                        and "claude" not in x.get("model", "") for x in r.get("attempts") or [])
                    and r.get("turns", 0) == 0)
        skipped = sum(1 for r in c if not (r.get("attempts") or []))       # routing sent it straight to the session model
        lat = lambda xs: f"{pct(xs, 50):5.0f}/{pct(xs, 95):5.0f}/{pct(xs, 99):5.0f}"  # noqa: E731
        out.append(f"{suite:8} {len(ts):>4} | {solved(a):>8} {ca:>7.2f} | {solved(c):>8} {cc:>7.2f} "
                   f"{(cc - ca) / ca * 100 if ca else 0:>6.0f}% | {lat(secs(a)):>17} | {lat(secs(c)):>17} | "
                   f"{cheap:>7}/{len(ts):<7} | {skipped}/{len(ts)}")
    wrong = [(t, rows[(t, "C")].get("suite")) for t in tasks if not rows[(t, "C")].get("resolved")]
    both = [t for t, _ in wrong if not rows[(t, "A")].get("resolved")]
    out.append(f"\nC unsolved: {len(wrong)} (of which A also unsolved: {len(both)}): {[t for t, _ in wrong]}")
    return "\n".join(out)


if __name__ == "__main__":
    print(summarise(load(sys.argv[1:] or ["phase1"])))
