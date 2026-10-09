"""Replay routing policies against RECORDED DS-1000 outcomes (experiments/ds1000/runs2, arm A: no notes, 124 problems,
four models, each attempt with its visible check, gold grade and cost). No model is called: a policy picks which
recorded attempts it would have made, in order, and is scored on what those attempts actually did.

Policies: the session model alone; a fixed cheap-first cascade; the router with the old rule (a ladder must meet the
target in >= 90% of the posterior) and the new rule (cheapest ladder meeting the target in one sampled draw). Both
router rules use the matched target (the session model alone, minus 0.03), learn from earlier problems' outcomes
(local_obs, as the product does with .stealth/routing.md), and pay no hand-off cost (models are called directly here).
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "local_eval"))
sys.path.insert(0, str(ROOT / "backend"))
from route_run import _no_database  # noqa: E402
from app.routing import service  # noqa: E402
from app.services.access import AccessScope  # noqa: E402

RUN = ROOT / "experiments" / "ds1000" / "runs2" / "attempts.jsonl"
# recorded model name -> router unit (scaffold "direct": a plain model call, a scaffold the prior knows)
UNITS = {"gemma-4-31B-it": "gemma-4-31b-it|direct", "gpt-oss-120b": "gpt-oss-120b|direct",
         "deepseek-v3.2": "deepseek-v3-2|direct", "claude-sonnet-5": "claude-sonnet-5|direct"}
SESSION = "claude-sonnet-5|direct"


def load() -> dict[str, dict[str, dict]]:
    by = collections.defaultdict(dict)
    for line in RUN.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("arm") == "A" and r.get("notes_ref") is None and r.get("call_error") is None and r["model"] in UNITS:
            by[r["problem_id"]][UNITS[r["model"]]] = {"check": bool(r["check_pass"]), "gold": bool(r["gold_pass"]),
                                                       "cost": float(r.get("cost_usd") or 0.0),
                                                       "tin": int(r.get("tokens_in") or 1), "tout": int(r.get("tokens_out") or 1)}
    return {pid: v for pid, v in by.items() if len(v) == len(UNITS)}


def deliver(rec: dict, ladder: list[str]) -> dict:
    """Run the ladder on the recorded outcomes: stop at the first attempt whose check passes."""
    cost, tried = 0.0, []
    for u in ladder:
        a = rec[u]
        cost += a["cost"]
        tried.append((u, a["check"]))
        if a["check"]:
            return {"solved": a["gold"], "wrong": not a["gold"], "cost": cost, "tried": tried}
    return {"solved": rec[ladder[-1]]["gold"], "wrong": False, "cost": cost, "tried": tried}


async def routed(problems: dict, feasibility: str, end_with_baseline: bool = True) -> list[dict]:
    """As the product: every attempt's outcome counts locally (OBS), and its tokens are reported (report_result ->
    routing_observations), from which the router learns each unit's real cost here (goal_token_stats)."""
    import math
    obs = collections.Counter()
    tokens: dict = collections.defaultdict(lambda: collections.defaultdict(list))     # unit -> outcome -> [(li, lo)]

    async def token_stats(_pool, _goal_id, *, steps=False):
        return {u: {o: [float(len(v)), sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v), 0.0]
                    for o, v in by.items() if v} for u, by in tokens.items()}
    out = []
    for i, (pid, rec) in enumerate(sorted(problems.items(), key=lambda kv: int(kv[0]))):
        local = [{"unit": u, "n": obs[(u, "n")], "ok": obs[(u, "ok")]} for u in UNITS.values() if obs[(u, "n")]]
        with _no_database():
            service.store.goal_token_stats = token_stats
            r = await service._recommend(
                None, goal_id=service.virtual_goal_id("repo", "ds1000"), candidates=list(UNITS.values()),
                access_scope=AccessScope.unrestricted(), virtual={"kind": "repo", "ref": "ds1000"},
                instance_key=f"replay-{feasibility}-{pid}", check_kind="tests", previous_attempts=[],
                local_obs=local, record=False,
                constraints={"reliability_baseline": SESSION, "feasibility": feasibility, "handoff_cost_usd": 0.0,
                             "end_with_baseline": end_with_baseline})
        res = deliver(rec, r["recommended"]["ladder"])
        for u, ok in res["tried"]:
            obs[(u, "n")] += 1
            obs[(u, "ok")] += int(ok)
            tokens[u]["1" if ok else "0"].append((math.log(max(rec[u]["tin"], 1)), math.log(max(rec[u]["tout"], 1))))
        out.append(res)
    return out


def summary(name: str, rows: list[dict], base_cost: float) -> str:
    n = len(rows)
    solved = sum(r["solved"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    first = collections.Counter(r["tried"][0][0].split("|")[0] for r in rows)
    return (f"{name:34} solved {solved:3}/{n} ({solved / n:.1%})  wrong delivered {sum(r['wrong'] for r in rows):3}  "
            f"cost ${cost:.3f} ({(cost / base_cost - 1):+.0%})  first: {dict(first.most_common(4))}")


async def main() -> None:
    problems = load()
    order = sorted(problems.items(), key=lambda kv: int(kv[0]))
    alone = [deliver(rec, [SESSION]) for _, rec in order]
    base = sum(r["cost"] for r in alone)
    cascade = [deliver(rec, ["gemma-4-31b-it|direct", "deepseek-v3-2|direct", SESSION]) for _, rec in order]
    cascade2 = [deliver(rec, ["gpt-oss-120b|direct", "gemma-4-31b-it|direct", "deepseek-v3-2|direct", SESSION])
                for _, rec in order]
    print(f"{len(problems)} problems, outcomes recorded in {RUN.relative_to(ROOT)}")
    print(summary("session model alone (Sonnet)", alone, base))
    print(summary("cascade gemma>deepseek>sonnet", cascade, base))
    print(summary("cascade gptoss>gemma>deepseek>sonnet", cascade2, base))
    print(summary("router, old rule (posterior 90%)", await routed(problems, "posterior", False), base))
    print(summary("router, cheapest per draw", await routed(problems, "draw", False), base))
    print(summary("router, per draw + ends with session", await routed(problems, "draw", True), base))
    print(summary("router, posterior + ends with session", await routed(problems, "posterior", True), base))


if __name__ == "__main__":
    asyncio.run(main())
