"""Graduation and retirement from evidence (docs/goal_router.md P3-04).

    stages = await evaluate(pool, goal="web")      # [Stage(card_id, stage, numbers...), ...]
    gone = await excluded(pool, "web")             # retired cards: goal_router leaves them out (P3-03)
    penalties = await demotions(pool, "web")       # retiring cards: listed, ranked lower

A stage is computed from the evidence every time (tool_trust, db/192, and the recent runs), so there is no state to
drift and a tool that recovers moves back up on its own:

    new        fewer than MIN_JUDGED judged attempts: no verdict yet (an unproven tool is never retired)
    graduate   judged >= 20 and lower bound >= 0.6
    watch      0.4 <= lower bound < 0.6
    retiring   lower bound < 0.4, or a recent failure rate above 30%, with less than RETIRE_JUDGED of evidence:
               still listed, ranked lower (RETIRING_PENALTY), so its traffic falls before it is delisted
    retired    the same, with at least RETIRE_JUDGED judged attempts: left out of the goal's tools; the next best tool
               leads the goal
    (a tool with a good lower bound but too little evidence to graduate stays "watch" until it has 20)

The recent window is the last RECENT_DAYS days of tool-arm runs and judged real use; it needs RECENT_MIN attempts
before its failure rate counts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

MIN_JUDGED = 10
GRADUATE_JUDGED = 20
GRADUATE_LB = 0.6
WATCH_LB = 0.4
RECENT_DAYS = 14
RECENT_MIN = 10
MAX_RECENT_FAILURE = 0.30
RETIRE_JUDGED = 30
RETIRING_PENALTY = 0.25


@dataclass
class Stage:
    card_id: str
    goal: str
    stage: str
    wins: int
    judged: int
    lower_bound: float
    recent_judged: int
    recent_failure_rate: Optional[float]
    why: str

    def to_dict(self) -> dict:
        return asdict(self)


def classify(wins: int, judged: int, lower_bound: float, recent_judged: int,
             recent_failures: int) -> tuple[str, str, Optional[float]]:
    """-> (stage, why, recent failure rate). Pure: the rules above, nothing else."""
    rate = recent_failures / recent_judged if recent_judged else None
    failing_recently = rate is not None and recent_judged >= RECENT_MIN and rate > MAX_RECENT_FAILURE
    if judged < MIN_JUDGED and not failing_recently:
        return "new", f"{judged} judged < {MIN_JUDGED}: no verdict yet", rate
    if lower_bound < WATCH_LB or failing_recently:
        why = (f"recent failure rate {rate:.0%} > {MAX_RECENT_FAILURE:.0%} over {recent_judged}" if failing_recently
               else f"lower bound {lower_bound:.3f} < {WATCH_LB}")
        if judged >= RETIRE_JUDGED:
            return "retired", why + f"; {judged} judged >= {RETIRE_JUDGED}: delisted", rate
        return "retiring", why + f"; {judged} judged < {RETIRE_JUDGED}: demoted before delisting", rate
    if lower_bound >= GRADUATE_LB and judged >= GRADUATE_JUDGED:
        return "graduate", f"{judged} judged >= {GRADUATE_JUDGED} and lower bound {lower_bound:.3f} >= {GRADUATE_LB}", rate
    if lower_bound >= GRADUATE_LB:
        return "watch", f"lower bound {lower_bound:.3f} >= {GRADUATE_LB} but {judged} judged < {GRADUATE_JUDGED}", rate
    return "watch", f"lower bound {lower_bound:.3f} between {WATCH_LB} and {GRADUATE_LB}", rate


async def _recent(pool: Any, goal_keys: list[str], card_ids: list[str]) -> dict[str, tuple[int, int]]:
    """card -> (judged, failures) in the recent window, from the same verified evidence tool_trust uses."""
    rows = await pool.fetch(
        """
        SELECT card_id, count(*)::int AS judged, count(*) FILTER (WHERE status <> 'pass')::int AS failures
        FROM task_runs
        WHERE arm = 'tool' AND card_id = ANY($2::text[]) AND goal_id = ANY($1::text[])
          AND status IN ('pass', 'fail', 'error')
          AND COALESCE(error_class, '') NOT LIKE 'gate:%' AND COALESCE(error_class, '') NOT IN ('no_tool', 'budget')
          AND started_at >= now() - make_interval(days => $3)
        GROUP BY card_id
        """,
        goal_keys, card_ids, RECENT_DAYS,
    )
    return {r["card_id"]: (r["judged"], r["failures"]) for r in rows}


async def evaluate(pool: Any, goal: str, *, limit: int = 1000) -> list[Stage]:
    from app.tool_routing import trust

    rows = await trust.rows(pool, goal=goal, limit=limit)
    if not rows:
        return []
    recent = await _recent(pool, trust._goal_keys(goal), [r["card_id"] for r in rows])
    out = []
    for r in rows:
        rj, rf = recent.get(r["card_id"], (0, 0))
        stage, why, rate = classify(r["wins"], r["judged"], r["lower_bound"], rj, rf)
        out.append(Stage(r["card_id"], goal, stage, r["wins"], r["judged"], round(r["lower_bound"], 4), rj,
                         None if rate is None else round(rate, 4), why))
    order = {"graduate": 0, "watch": 1, "new": 2, "retiring": 3, "retired": 4}
    out.sort(key=lambda s: (order[s.stage], -s.lower_bound, s.card_id))
    return out


async def _safe(pool: Any, goal: str) -> list[Stage]:
    try:
        return await evaluate(pool, goal)
    except Exception as exc:  # noqa: BLE001 -- no tool_trust yet (192 not applied): nothing is retired
        if type(exc).__name__ in ("UndefinedTableError", "UndefinedObjectError"):
            return []
        raise


async def excluded(pool: Any, goal: str) -> set[str]:
    """Retired cards for this goal: the router leaves them out."""
    return {s.card_id for s in await _safe(pool, goal) if s.stage == "retired"}


async def demotions(pool: Any, goal: str) -> dict[str, float]:
    """Retiring cards for this goal and the rank they lose."""
    return {s.card_id: RETIRING_PENALTY for s in await _safe(pool, goal) if s.stage == "retiring"}
