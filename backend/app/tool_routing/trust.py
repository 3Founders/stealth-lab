"""Trust from verified reuse: a Beta posterior per (tool card, goal) (db/192_tool_trust.sql, docs/goal_router.md P3-01).

    lb = lower_bound(wins=90, judged=100)                 # ~0.84
    bounds = await lower_bounds(pool, "web", card_ids)    # {card_id: lower bound}, for ranking

The prior is Beta(1, 1) (uniform); after w wins in n judged attempts the posterior is Beta(1 + w, 1 + n - w). Ranking
uses its lower 5% quantile, not the mean: 1 win in 1 attempt gives ~0.22, 90 in 100 gives ~0.84, so a card has to
earn a high rank with many verified passes. A card with no evidence gets the prior's bound (0.05), the same for all,
which leaves room for exploration (P3-02/P3-03) without letting a badge stand in for evidence.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

PRIOR_A = 1.0
PRIOR_B = 1.0
QUANTILE = 0.05


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes, betacf)."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """The regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(ln_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(ln_front) * _betacf(b, a, 1.0 - x) / b


def beta_quantile(q: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(80):                       # bisection: 80 halvings is far below float resolution
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def lower_bound(wins: int, judged: int, *, q: float = QUANTILE) -> float:
    wins, judged = max(0, int(wins)), max(0, int(judged))
    wins = min(wins, judged)
    return beta_quantile(q, PRIOR_A + wins, PRIOR_B + judged - wins)


def _goal_keys(goal: Optional[str]) -> list[str]:
    """A goal as stored in the view: its taxonomy key and, for a key, the Goal uuid too (runs may carry either)."""
    if not goal:
        return [""]
    import uuid

    keys = [str(goal)]
    try:
        uuid.UUID(str(goal))
    except ValueError:
        keys.append(str(uuid.uuid5(uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), f"kell:tool-goal:{goal}")))
    return keys


async def rows(pool: Any, *, goal: Optional[str] = None, card_ids: Optional[Iterable[str]] = None,
               limit: int = 100) -> list[dict]:
    """The view's rows (merged across a goal's key and uuid), each with its exact lower bound, best first."""
    ids = list(card_ids) if card_ids is not None else None
    raw = await pool.fetch(
        """
        SELECT card_id, sum(wins)::int AS wins, sum(judged)::int AS judged, max(last_at) AS last_at,
               (array_agg(last_outcome ORDER BY last_at DESC))[1] AS last_outcome,
               sum(from_comparisons)::int AS from_comparisons, sum(from_use)::int AS from_use,
               sum(trial_calls)::int AS trial_calls, sum(paid_calls)::int AS paid_calls
        FROM tool_trust
        WHERE ($1::text[] IS NULL OR goal = ANY($1::text[]))
          AND ($2::text[] IS NULL OR card_id = ANY($2::text[]))
        GROUP BY card_id
        """,
        _goal_keys(goal) if goal is not None else None, ids,
    )
    out = [{**dict(r), "lower_bound": lower_bound(r["wins"], r["judged"])} for r in raw]
    out.sort(key=lambda r: (-r["lower_bound"], -r["judged"], r["card_id"]))
    return out[: max(1, int(limit))]


async def lower_bounds(pool: Any, goal: str, card_ids: list[str]) -> dict[str, float]:
    """{card_id: lower bound} for the given cards under a goal; a card without evidence gets the prior's bound."""
    try:
        found = {r["card_id"]: r["lower_bound"] for r in await rows(pool, goal=goal, card_ids=card_ids,
                                                                     limit=len(card_ids) or 1)}
    except Exception as exc:  # noqa: BLE001 -- no view yet (192 not applied): every card is at the prior
        if type(exc).__name__ not in ("UndefinedTableError", "UndefinedObjectError"):
            raise
        found = {}
    prior = lower_bound(0, 0)
    return {c: found.get(c, prior) for c in card_ids}


async def refresh(pool: Any) -> None:
    await pool.execute("SELECT refresh_tool_trust()")
