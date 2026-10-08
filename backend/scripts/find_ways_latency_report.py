"""Where the time of `find_ways` goes: p50/p90/p99 per stage, from what the server already records.

Every `find_ways` call writes one `retrieval_decisions` row (mode 'find_ways') whose `detail` holds `total_ms` and
`stages`: the per-stage timing from app/utils/stage_timer.py (governor, triage_judge, goal_search_legs, embed,
judge_goal, hierarchy, procedure_tier, procedure_fetch, selector_judge, model_plan, ...). Read-only. Run it against the
database the server logs to (SEARCH_DATABASE_URL when set, else DATABASE_URL):

    python scripts/find_ways_latency_report.py                 # the last 7 days
    python scripts/find_ways_latency_report.py --days 1 --outcome resolved
    python scripts/find_ways_latency_report.py --json          # for a dashboard or a diff between two releases

How to read it. A stage's `ms` is the SUM over its calls (`calls` per request), so with parallel calls the stages can
add up to more than the request's wall time: compare stages with each other and with `total`, not as a partition.
`share` is a stage's time summed over every request, as a fraction of the requests' summed `total_ms` (a stage a
request never ran counts as zero there); `n` is how many requests had that stage at all, `calls` the mean calls per request. Requests
with no `stages` (recorded before this existed, or a refusal that returned early) are counted but contribute no stage rows.
Only fixed stage names are stored, never a query, id or any request text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))      # run from anywhere: `app` lives in backend/


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile (no interpolation: a latency report should show a number that happened)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(p / 100.0 * len(ordered) + 0.4999999))))
    return ordered[rank - 1]


def summarize(rows: Iterable[Mapping[str, Any]], *, outcome: Optional[str] = None) -> dict:
    """`rows`: dicts with `detail` (a dict, or its JSON text). Returns {"requests", "total": {...}, "stages": [...]}."""
    totals: list[float] = []
    stages: dict[str, dict[str, list[float]]] = {}
    outcomes: dict[str, int] = {}
    for row in rows:
        detail = row.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                continue
        if not isinstance(detail, dict):
            continue
        row_outcome = str(detail.get("outcome") or "unknown")
        if outcome and row_outcome != outcome:
            continue
        outcomes[row_outcome] = outcomes.get(row_outcome, 0) + 1
        total = detail.get("total_ms")
        if isinstance(total, (int, float)):
            totals.append(float(total))
        for name, entry in (detail.get("stages") or {}).items():
            if not isinstance(entry, dict) or not isinstance(entry.get("ms"), (int, float)):
                continue
            bucket = stages.setdefault(name, {"ms": [], "calls": [], "max": []})
            bucket["ms"].append(float(entry["ms"]))
            bucket["calls"].append(float(entry.get("n") or 1))
            bucket["max"].append(float(entry.get("max_ms") or entry["ms"]))
    mean_total = statistics.fmean(totals) if totals else 0.0
    sum_total = sum(totals)
    table = []
    for name, b in stages.items():
        table.append({
            "stage": name, "n": len(b["ms"]), "p50": round(_pct(b["ms"], 50), 1), "p90": round(_pct(b["ms"], 90), 1),
            "p99": round(_pct(b["ms"], 99), 1), "mean": round(statistics.fmean(b["ms"]), 1),
            "calls": round(statistics.fmean(b["calls"]), 2), "slowest_call": round(max(b["max"]), 1),
            "share": round(sum(b["ms"]) / sum_total, 3) if sum_total else None,
        })
    table.sort(key=lambda r: -r["mean"])
    return {
        "requests": sum(outcomes.values()), "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        "total": {"n": len(totals), "p50": round(_pct(totals, 50), 1), "p90": round(_pct(totals, 90), 1),
                  "p99": round(_pct(totals, 99), 1), "mean": round(mean_total, 1)},
        "stages": table,
    }


def render(summary: dict) -> str:
    out = [f"requests: {summary['requests']}   outcomes: {summary['outcomes']}"]
    t = summary["total"]
    out.append(f"total_ms  n={t['n']}  p50={t['p50']}  p90={t['p90']}  p99={t['p99']}  mean={t['mean']}")
    out.append("")
    out.append(f"{'stage':<22}{'n':>6}{'p50':>9}{'p90':>9}{'p99':>9}{'mean':>9}{'calls':>7}{'share':>8}{'slowest':>9}")
    for r in summary["stages"]:
        share = "-" if r["share"] is None else f"{r['share'] * 100:.0f}%"
        out.append(f"{r['stage']:<22}{r['n']:>6}{r['p50']:>9}{r['p90']:>9}{r['p99']:>9}{r['mean']:>9}"
                   f"{r['calls']:>7}{share:>8}{r['slowest_call']:>9}")
    if not summary["stages"]:
        out.append("(no row has a stage breakdown yet: the server records it from the release that added "
                   "app/utils/stage_timer.py)")
    return "\n".join(out)


async def _fetch(days: float, limit: int) -> list[dict]:
    from app.db.session import create_pool
    from app.services.shards import search_pool

    pool = await create_pool()
    try:
        logs = await search_pool(pool)
        rows = await logs.fetch(
            "SELECT detail FROM retrieval_decisions WHERE mode = 'find_ways' "
            "AND created_at > now() - ($1 * interval '1 day') ORDER BY created_at DESC LIMIT $2", float(days), limit)
        return [dict(r) for r in rows]
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--limit", type=int, default=20000, help="most recent rows to read")
    ap.add_argument("--outcome", help="only this outcome (resolved, ambiguous, no_match, not_needed, ...)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (os.environ.get("DATABASE_URL") or os.environ.get("SEARCH_DATABASE_URL")):
        from app.config import settings

        if not getattr(settings, "database_url", None):
            print("no DATABASE_URL (or SEARCH_DATABASE_URL) is set", file=sys.stderr)
            return 2
    summary = summarize(asyncio.run(_fetch(args.days, args.limit)), outcome=args.outcome)
    print(json.dumps(summary, indent=2) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
