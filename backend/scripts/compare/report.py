"""A Markdown comparison report per goal, from the stored runs (docs/goal_router.md P2-07).

    python -m scripts.compare.report --goal web --out docs/compare_web.md
    python -m scripts.compare.report --goal database --since 7d

Reads task_runs (one row per attempt) for the goal's corpus tasks, pairs the arms on the same task and attempt, and
reports: a table per arm (attempts, pass rate, mean score, cost, latency, cost per verified success, errors), the
paired tool-minus-procedure (and baseline) deltas with bootstrap 95% CIs, McNemar on pass/fail, a per-task table,
every failure grouped by arm and error class, and the caveats. No arm or failure is left out, and there is no
verdict without a CI that excludes zero. Held-out tasks are reported separately.
Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from scripts.compare.contracts import ARMS, GOALS
from scripts.compare.corpus import load
from scripts.compare.stats import cost_per_success, describe_cost, mcnemar, paired_bootstrap

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_PAIRS_FOR_VERDICT = 30
ABSENT = ("unbound", "no_procedure", "empty_procedure", "no_tool")


async def fetch_runs(pool: Any, task_ids: list[str], since_s: Optional[int]) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT task_id, arm, attempt, status, score, cost_usd::float8 AS cost_usd, latency_ms, error_class, card_id,
               procedure_id, started_at
        FROM task_runs
        WHERE task_id = ANY($1::text[]) AND status <> 'running'
          AND ($2::int IS NULL OR started_at >= now() - make_interval(secs => $2))
        ORDER BY task_id, arm, attempt
        """,
        task_ids, since_s,
    )
    return [dict(r) for r in rows]


def pairs(runs: list[dict], a: str, b: str) -> list[tuple[dict, dict]]:
    """(run of arm a, run of arm b) for the same task, matched by order of attempt within each arm."""
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        by[(r["task_id"], r["arm"])].append(r)
    out = []
    for task in sorted({r["task_id"] for r in runs}):
        xs, ys = by.get((task, a), []), by.get((task, b), [])
        out.extend(zip(xs, ys))
    return out


def arm_table(runs: list[dict], arms: list[str]) -> list[str]:
    lines = ["| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | "
             "errors |", "|---|---|---|---|---|---|---|---|"]
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        if not rs:
            lines.append(f"| {arm} | 0 | - | - | - | - | - | - |")
            continue
        passed = [r["status"] == "pass" for r in rs]
        errors = sum(r["status"] == "error" for r in rs)
        costs = [float(r["cost_usd"] or 0) for r in rs]
        lat = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        lines.append(f"| {arm} | {len(rs)} | {sum(passed) / len(rs):.0%} | {mean(float(r['score'] or 0) for r in rs):.2f} | "
                     f"{mean(costs):.4f} | {mean(lat) if lat else 0:.0f} | {describe_cost(cost_per_success(costs, passed))} | "
                     f"{errors} |")
    return lines


def paired_section(runs: list[dict], a: str, b: str) -> tuple[list[str], Optional[str]]:
    ps = pairs(runs, a, b)
    title = f"### {a} minus {b} (paired on task and attempt)"
    if not ps:
        return [title, "", f"No pairs: {a} and {b} were not both run on any task.", ""], None
    score_ci = paired_bootstrap([float(x["score"] or 0) - float(y["score"] or 0) for x, y in ps])
    cost_ci = paired_bootstrap([float(x["cost_usd"] or 0) - float(y["cost_usd"] or 0) for x, y in ps])
    lat_ci = paired_bootstrap([float(x["latency_ms"] or 0) - float(y["latency_ms"] or 0) for x, y in ps])
    m = mcnemar([(x["status"] == "pass", y["status"] == "pass") for x, y in ps])
    if score_ci.n < 2:
        verdict = f"no verdict: {score_ci.n} pair"
    elif score_ci.low > 0:
        verdict = f"{a} scores higher than {b} (95% CI excludes 0)"
    elif score_ci.high < 0:
        verdict = f"{b} scores higher than {a} (95% CI excludes 0)"
    else:
        verdict = f"no difference shown between {a} and {b} (95% CI contains 0)"
    if score_ci.n < MIN_PAIRS_FOR_VERDICT and "no verdict" not in verdict:
        verdict += f"; only {score_ci.n} pairs (< {MIN_PAIRS_FOR_VERDICT}), treat as preliminary"
    lines = [title, "", "| measure | estimate [95% CI] |", "|---|---|",
             f"| score delta | {score_ci} |", f"| cost delta $ | {cost_ci} |", f"| latency delta ms | {lat_ci} |",
             f"| pass/fail (McNemar) | {m} |", "", f"**{verdict}.**", ""]
    return lines, verdict


def per_task(runs: list[dict], arms: list[str]) -> list[str]:
    lines = ["| task | " + " | ".join(f"{a} pass/attempts" for a in arms) + " | tool card | procedure |",
             "|---|" + "---|" * len(arms) + "---|---|"]
    for task in sorted({r["task_id"] for r in runs}):
        rs = [r for r in runs if r["task_id"] == task]
        cells = []
        for a in arms:
            ar = [r for r in rs if r["arm"] == a]
            cells.append(f"{sum(r['status'] == 'pass' for r in ar)}/{len(ar)}" if ar else "-")
        card = next((r["card_id"] for r in rs if r["card_id"]), "-")
        proc = next((r["procedure_id"] for r in rs if r["procedure_id"]), "-")
        lines.append(f"| {task} | " + " | ".join(cells) + f" | {card} | {proc} |")
    return lines


def failures(runs: list[dict], arms: list[str]) -> list[str]:
    lines = []
    for a in arms:
        bad = [r for r in runs if r["arm"] == a and r["status"] != "pass"]
        if not bad:
            lines.append(f"- **{a}**: none")
            continue
        groups: dict[str, list[str]] = defaultdict(list)
        for r in bad:
            groups[f"{r['status']}: {r['error_class'] or 'wrong answer'}"].append(f"{r['task_id']}#{r['attempt']}")
        lines.append(f"- **{a}**: {len(bad)} of {sum(r['arm'] == a for r in runs)}")
        for why, where in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  - {why} ({len(where)}): {', '.join(where[:20])}{' ...' if len(where) > 20 else ''}")
    return lines


def caveats(runs: list[dict], arms: list[str], n_pairs: int) -> list[str]:
    out = []
    if n_pairs < MIN_PAIRS_FOR_VERDICT:
        out.append(f"Only {n_pairs} tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is "
                   "preliminary.")
    absent = Counter(r["error_class"] for r in runs if r["arm"] == "procedure" and r["error_class"] in ABSENT)
    if absent:
        out.append("The procedure arm did not run on some attempts: " + ", ".join(f"{k} {v}" for k, v in absent.items())
                   + ". These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a "
                     "procedure'.")
    denied = sum(1 for r in runs if r["arm"] == "tool" and (r["error_class"] or "").startswith("gate:"))
    if denied:
        out.append(f"The gate refused {denied} tool attempts (no policy row, or a certificate/risk refusal): the tool "
                   "arm measures what kell is allowed to run, not every card's capability.")
    attempts = Counter((r["task_id"], r["arm"]) for r in runs)
    if attempts and min(attempts.values()) < 3:
        out.append("Some task/arm cells have fewer than 3 attempts.")
    out.append("Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used "
               "unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the "
               "tool surfaced the answer, not that an agent used it correctly.")
    return out


async def build(pool: Any, goal: str, since_s: Optional[int]) -> str:
    tasks = load(goal=goal)
    holdout = {t.id for t in tasks if t.holdout}
    runs = await fetch_runs(pool, [t.id for t in tasks], since_s)
    arms = [a for a in ARMS if any(r["arm"] == a for r in runs)] or ["procedure", "tool"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Tool vs procedure: {goal}", "",
             f"Generated {now} by `python -m scripts.compare.report --goal {goal}` from task_runs. "
             f"{len(tasks)} corpus tasks ({len(holdout)} held out), {len({r['task_id'] for r in runs})} with runs, "
             f"{len(runs)} attempts.", ""]
    if not runs:
        lines += ["No runs for this goal yet. Run `python -m scripts.compare.run_compare --goal " + goal + "`.", ""]
        return "\n".join(lines)
    main_runs = [r for r in runs if r["task_id"] not in holdout]
    held_runs = [r for r in runs if r["task_id"] in holdout]
    verdicts = []
    for label, rs in (("Main tasks", main_runs), ("Held-out tasks", held_runs)):
        lines += [f"## {label}", ""]
        if not rs:
            lines += ["No runs.", ""]
            continue
        lines += ["### Arms", ""] + arm_table(rs, arms) + [""]
        for a, b in (("tool", "procedure"), ("tool", "baseline"), ("procedure", "baseline")):
            if a in arms and b in arms:
                sec, verdict = paired_section(rs, a, b)
                lines += sec
                if verdict and label == "Main tasks":
                    verdicts.append(verdict)
        lines += ["### Per task", ""] + per_task(rs, arms) + [""]
    lines += ["## Every failure", ""] + failures(runs, arms) + [""]
    n_pairs = len(pairs(main_runs, "tool", "procedure"))
    lines += ["## Caveats", ""] + [f"- {c}" for c in caveats(runs, arms, n_pairs)] + [""]
    lines[3:3] = ["**Answer:** " + ("; ".join(verdicts) if verdicts else "no paired comparison yet") + ".", ""]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    from scripts.compare.procedure_runner import dsn

    url = dsn(args.dsn)
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    since = None
    if args.since:
        m = re.fullmatch(r"(\d+)([smhd])", args.since.strip())
        if not m:
            raise SystemExit("--since expects 30m, 1h, 7d")
        since = int(m.group(1)) * UNITS[m.group(2)]
    import asyncpg

    conn = await asyncpg.connect(url, timeout=30)
    try:
        text = await build(conn, args.goal, since)
    finally:
        await conn.close()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser(description="Write the comparison report for one goal.")
    ap.add_argument("--goal", required=True, choices=GOALS)
    ap.add_argument("--since", help="30m, 1h, 7d (default: every run)")
    ap.add_argument("--out")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
