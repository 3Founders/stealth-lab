"""The tool market's weekly report (docs/goal_router.md P3-07).

    python scripts/tool_market_report.py --since 7d --out docs/tool_market_results.md
    python scripts/tool_market_report.py --since 30d --goal web

Every number comes with its sample size, and a metric without data says "no data" instead of a zero:

  exploration rate        tool-arm attempts and gateway calls that went to cards with < 10 judged attempts, or to a trial
  graduation rate         cards that reached "graduate" (app/tool_routing/lifecycle.py) out of the cards with evidence
  time to graduation      first verified attempt -> the attempt at which a card first met the graduation bar
  failure and harm rate   verified tool attempts that failed; gate decisions that denied or asked for approval
                          (tool_call_audit), and the R3+ share of them
  replacement rate        retired cards, and whether their goal now has another card leading it
  provider credit spend   trial credit spent (tool_trials) and listing charges (tool_charges) by who paid
  cost per verified success  tool-arm spend / passes
  trust calibration       does the lower bound predict the pass rate? cards binned by the lower bound from evidence
                          BEFORE the window, against their observed pass rate INSIDE it (a calibrated bound sits at or
                          below the observed rate in every bin)

Database: --dsn, else DATABASE_URL in the environment, else backend/.env. Read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from app.tool_routing import lifecycle, trust

UNITS = {"h": 3600, "d": 86400}
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
VERIFIED = """arm = 'tool' AND card_id IS NOT NULL AND status IN ('pass', 'fail', 'error')
              AND COALESCE(error_class, '') NOT LIKE 'gate:%' AND COALESCE(error_class, '') NOT IN ('no_tool', 'budget')"""


async def optional(conn: Any, sql: str, *args: Any) -> list[Any]:
    try:
        return await conn.fetch(sql, *args)
    except Exception as exc:  # noqa: BLE001 -- a table a later migration adds: no data, said as such
        if type(exc).__name__ in ("UndefinedTableError", "UndefinedColumnError"):
            return []
        raise


def pct(a: int, b: int) -> str:
    return f"{a / b:.0%} ({a}/{b})" if b else "no data"


async def build(conn: Any, since_s: int, goal: Optional[str]) -> str:
    from app.tool_routing.goal_router import _goal_key   # a tool goal's uuid and its key are one goal

    goals = [goal] if goal else sorted({_goal_key(str(r["goal"])) for r in await optional(
        conn, "SELECT DISTINCT goal FROM tool_trust WHERE goal <> ''")})
    goal_keys = sorted({k for g in goals for k in trust._goal_keys(g)})
    now = datetime.now(timezone.utc)
    lines = [f"# Tool market report", "",
             f"Generated {now:%Y-%m-%d %H:%M} UTC by `python scripts/tool_market_report.py --since "
             f"{since_s // 86400 if since_s >= 86400 else since_s // 3600}{'d' if since_s >= 86400 else 'h'}"
             f"{' --goal ' + goal if goal else ''}`. Window: the last {since_s / 86400:.1f} days. Goals: "
             f"{', '.join(goals) if goals else 'none with evidence'}.", ""]

    runs = await optional(conn, f"""SELECT card_id, goal_id, status, cost_usd::float8 AS cost, started_at FROM task_runs
                                    WHERE {VERIFIED} AND goal_id = ANY($1::text[]) ORDER BY started_at""", goal_keys)
    window = [r for r in runs if (now - r["started_at"]).total_seconds() <= since_s]
    before = [r for r in runs if (now - r["started_at"]).total_seconds() > since_s]

    # exploration
    judged_before: dict[str, int] = defaultdict(int)
    for r in before:
        judged_before[r["card_id"]] += 1
    trial_cards = {str(r["card_id"]) for r in await optional(conn, "SELECT card_id FROM tool_trials WHERE goal = ANY($1::text[])",
                                                               goal_keys)}
    explored = sum(1 for r in window if judged_before[r["card_id"]] < lifecycle.MIN_JUDGED or r["card_id"] in trial_cards)
    lines += ["## Exploration", "",
              f"- Exploration rate: {pct(explored, len(window))} of verified tool attempts in the window went to a card "
              f"with fewer than {lifecycle.MIN_JUDGED} judged attempts before it, or to a trial card.",
              f"- Cards tried in the window: {len({r['card_id'] for r in window})}; with a trial: {len(trial_cards)}.", ""]

    # graduation, retirement, replacement
    stages = [s for g in goals for s in await lifecycle.evaluate(conn, g)]
    with_evidence = [s for s in stages if s.stage != "new"]
    graduated = [s for s in stages if s.stage == "graduate"]
    retired = [s for s in stages if s.stage == "retired"]
    ttg = []
    for s in graduated:
        seq = [r for r in runs if r["card_id"] == s.card_id]
        wins = 0
        for i, r in enumerate(seq, 1):
            wins += r["status"] == "pass"
            if i >= lifecycle.GRADUATE_JUDGED and trust.lower_bound(wins, i) >= lifecycle.GRADUATE_LB:
                ttg.append(((r["started_at"] - seq[0]["started_at"]).total_seconds() / 86400, i))
                break
    replaced = 0
    for s in retired:
        leaders = [x for x in stages if x.goal == s.goal and x.stage in ("graduate", "watch") and x.card_id != s.card_id]
        replaced += bool(leaders)
    lines += ["## Graduation and retirement", "",
              f"- Graduation rate: {pct(len(graduated), len(with_evidence))} of cards past the 'new' stage.",
              "- Time to graduation: " + (f"median {sorted(t for t, _ in ttg)[len(ttg) // 2]:.1f} days, "
                                          f"{sorted(n for _, n in ttg)[len(ttg) // 2]} judged attempts (n={len(ttg)} "
                                          "cards with a run history)" if ttg else "no data (no graduation seen in "
                                                                                  "run history)") + ".",
              f"- Stages now: " + ", ".join(f"{k} {sum(1 for s in stages if s.stage == k)}"
                                             for k in ("graduate", "watch", "new", "retiring", "retired")) + ".",
              ("- Replacement rate: " + (f"{pct(replaced, len(retired))} of retired cards have another card leading "
                                         "their goal." if retired else "no data (no card is retired).")),
              ""]

    # failure and harm
    fails = sum(1 for r in window if r["status"] != "pass")
    audit = await optional(conn, "SELECT decision, risk FROM tool_call_audit WHERE at >= now() - make_interval(secs => $1)",
                           since_s)
    refused = [a for a in audit if a["decision"] in ("deny", "approve")]
    high = sum(1 for a in refused if str(a["risk"]) in ("R3", "R4"))
    lines += ["## Failure and harm", "",
              f"- Failure rate: {pct(fails, len(window))} of verified tool attempts in the window failed.",
              f"- Gate decisions in the window: {len(audit)}; denied or sent for approval: {pct(len(refused), len(audit))}; "
              f"of those, R3/R4: {pct(high, len(refused))}.",
              "- Harm: no harm signal is recorded yet beyond gate refusals (no incident table); this line stays explicit "
              "until one exists.", ""]

    # money
    trial_spend = await optional(conn, "SELECT provider, sum(u.cost_usd)::float8 AS spent, count(*) AS calls FROM tool_trial_uses u "
                                       "JOIN tool_trials t ON t.id = u.trial_id WHERE u.at >= now() - make_interval(secs => $1) "
                                       "GROUP BY provider ORDER BY spent DESC", since_s)
    charges = await optional(conn, "SELECT paid_by, state, count(*) AS n, sum(price_usd)::float8 AS usd, "
                                   "sum(provider_usd)::float8 AS provider_usd, sum(kell_usd)::float8 AS kell_usd FROM tool_charges "
                                   "WHERE at >= now() - make_interval(secs => $1) GROUP BY paid_by, state ORDER BY paid_by, state",
                             since_s)
    spend = sum(float(r["cost"] or 0) for r in window)
    passes = sum(1 for r in window if r["status"] == "pass")
    lines += ["## Money (credits only)", "",
              "- Provider-funded trial spend: " + ("; ".join(f"{r['provider']} ${r['spent']:.4f} over {r['calls']} calls"
                                                             for r in trial_spend) if trial_spend else "no data") + ".",
              "- Listing charges: " + ("; ".join(f"{r['paid_by']}/{r['state']}: {r['n']} calls, ${r['usd']:.4f} "
                                                 f"(provider ${r['provider_usd']:.4f}, kell ${r['kell_usd']:.4f})"
                                                 for r in charges) if charges else "no data") + ".",
              f"- Cost per verified success (tool arm): " + (f"${spend / passes:.4f} (${spend:.4f} / {passes} passes)"
                                                             if passes else "no verified success") + ".", ""]

    # calibration
    lines += ["## Trust calibration", "",
              "Cards binned by their trust lower bound from evidence before the window; observed pass rate inside it. "
              "A calibrated bound is at or below the observed rate.", "",
              "| lower bound bin | cards | attempts in window | mean lower bound | observed pass rate | calibrated |",
              "|---|---|---|---|---|---|"]
    per_card: dict[str, list[Any]] = defaultdict(list)
    for r in window:
        per_card[r["card_id"]].append(r)
    wins_before: dict[str, int] = defaultdict(int)
    for r in before:
        wins_before[r["card_id"]] += r["status"] == "pass"
    for lo, hi in BINS:
        cards = [c for c in per_card if lo <= trust.lower_bound(wins_before[c], judged_before[c]) < hi]
        attempts = [r for c in cards for r in per_card[c]]
        if not attempts:
            lines.append(f"| {lo:.1f}-{min(hi, 1.0):.1f} | 0 | 0 | - | - | no data |")
            continue
        mean_lb = sum(trust.lower_bound(wins_before[c], judged_before[c]) for c in cards) / len(cards)
        observed = sum(r["status"] == "pass" for r in attempts) / len(attempts)
        lines.append(f"| {lo:.1f}-{min(hi, 1.0):.1f} | {len(cards)} | {len(attempts)} | {mean_lb:.3f} | {observed:.0%} | "
                     f"{'yes' if mean_lb <= observed else 'NO: the bound is above what happened'} |")
    lines += ["", "## Caveats", "",
              f"- {len(window)} verified tool attempts in the window, {len(before)} before it. Rates on fewer than 30 "
              "attempts are indicative only.",
              "- Verified attempts are comparison runs (task_runs) only; real-use observations count in tool_trust but "
              "carry no timestamps per card here.", ""]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = args.dsn or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    m = re.fullmatch(r"(\d+)([hd])", args.since.strip())
    if not m:
        raise SystemExit("--since expects 24h or 7d")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        text = await build(conn, int(m.group(1)) * UNITS[m.group(2)], args.goal)
    finally:
        await conn.close()
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="The tool market report.")
    ap.add_argument("--since", default="7d")
    ap.add_argument("--goal")
    ap.add_argument("--out")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
