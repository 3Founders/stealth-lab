"""Provider-funded trials: a capped, opt-in share of a goal's calls for a new tool (db/193, docs/goal_router.md P3-02).

    await status("web")                                         # active trials, credit left, share, uses
    trial = await offer(pool, "web", owner_id="u1", request_id="r42")   # this request's trial card, or None
    ok, why = await consume(pool, trial["id"], owner_id="u1", cost_usd=0.004)   # after the person opted in and it ran

offer() decides, deterministically per (owner, request), whether this request falls inside a trial's share of the
goal, and only offers a trial that still has credit, calls, time and room under the person's cap. The person sees
"New - free trial" and opts in; nothing runs on a trial without that. consume() debits the provider in one guarded
statement: it refuses (and changes nothing) when the credit, the call cap, the person's cap or the window would be
broken, so credit never goes negative even under concurrent calls. expire() closes trials whose window ended.
Only R0/R1 tools, or a read-only R2/R3 surface, can have a trial at all (the table refuses anything else).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

MAX_TRIALS_PER_GOAL = 2      # at most two trial cards compete for one goal's share at a time


def _bucket(owner_id: str, request_id: str) -> float:
    """A stable number in [0, 1) for this request: the same request always lands in the same place."""
    h = hashlib.sha256(f"{owner_id}\n{request_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def _goal_keys(goal: str) -> list[str]:
    from app.tool_routing.trust import _goal_keys as keys

    return keys(goal)


async def expire(pool: Any) -> int:
    """Close trials whose window ended or whose credit/calls are used up. -> how many changed."""
    done = await pool.fetch(
        """
        UPDATE tool_trials
        SET status = CASE WHEN window_end <= now() THEN 'expired' ELSE 'exhausted' END
        WHERE status = 'active'
          AND (window_end <= now() OR used_calls >= max_calls OR spent_usd >= credit_usd)
        RETURNING id
        """)
    return len(done)


async def active(pool: Any, goal: str) -> list[dict]:
    await expire(pool)
    rows = await pool.fetch(
        """
        SELECT id, provider, card_id, goal, credit_usd::float8 AS credit_usd, spent_usd::float8 AS spent_usd,
               (credit_usd - spent_usd)::float8 AS credit_left, max_calls, used_calls, per_user_calls, share,
               risk, read_only, window_start, window_end, status
        FROM tool_trials
        WHERE status = 'active' AND goal = ANY($1::text[]) AND window_start <= now() AND window_end > now()
        ORDER BY created_at, id
        LIMIT $2
        """,
        _goal_keys(goal), MAX_TRIALS_PER_GOAL,
    )
    return [dict(r) for r in rows]


async def offer(pool: Any, goal: str, *, owner_id: str, request_id: str) -> Optional[dict]:
    """The trial this request may be offered, or None. The goal's trials split their shares in order: with shares 0.1
    and 0.05, a request in [0, 0.1) is offered the first and one in [0.1, 0.15) the second; the rest see no trial."""
    if not owner_id:
        return None
    x = _bucket(owner_id, request_id)
    edge = 0.0
    for trial in await active(pool, goal):
        lo, edge = edge, edge + float(trial["share"])
        if not (lo <= x < edge):
            continue
        used = await pool.fetchval("SELECT count(*) FROM tool_trial_uses WHERE trial_id = $1 AND owner_id = $2",
                                   trial["id"], owner_id)
        if used >= trial["per_user_calls"] or trial["used_calls"] >= trial["max_calls"] or trial["credit_left"] <= 0:
            return None
        return {**trial, "label": "New - free trial", "uses_left_for_you": trial["per_user_calls"] - used}
    return None


async def consume(pool: Any, trial_id: int, *, owner_id: str, cost_usd: float) -> tuple[bool, str]:
    """Debit one opted-in trial call. -> (True, "ok") or (False, why); a refusal changes nothing."""
    cost = max(0.0, float(cost_usd or 0.0))
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE tool_trials t
                SET spent_usd = spent_usd + $3, used_calls = used_calls + 1
                WHERE t.id = $1 AND t.status = 'active' AND now() >= t.window_start AND now() < t.window_end
                  AND t.spent_usd + $3 <= t.credit_usd
                  AND t.used_calls < t.max_calls
                  AND (SELECT count(*) FROM tool_trial_uses u WHERE u.trial_id = t.id AND u.owner_id = $2)
                      < t.per_user_calls
                RETURNING id
                """,
                trial_id, owner_id, cost,
            )
            if row is None:
                state = await conn.fetchrow(
                    """SELECT status, now() >= window_end AS ended, spent_usd + $2 > credit_usd AS broke,
                              used_calls >= max_calls AS full,
                              (SELECT count(*) FROM tool_trial_uses u WHERE u.trial_id = $1 AND u.owner_id = $3)
                                  >= per_user_calls AS capped
                       FROM tool_trials WHERE id = $1""", trial_id, cost, owner_id)
                if state is None:
                    return False, "trial:not_found"
                for key, why in (("ended", "trial:expired"), ("broke", "trial:no_credit"), ("full", "trial:no_calls"),
                                 ("capped", "trial:user_cap")):
                    if state[key]:
                        return False, why
                return False, f"trial:{state['status']}"
            await conn.execute("INSERT INTO tool_trial_uses (trial_id, owner_id, cost_usd) VALUES ($1, $2, $3)",
                               trial_id, owner_id, cost)
    return True, "ok"


async def status(goal: str, pool: Any = None) -> list[dict]:
    """Active trials for a goal with credit left, calls left and share (opens its own connection when not given one:
    python -c "import asyncio; from app.tool_routing.trials import status; print(asyncio.run(status('web')))")."""
    if pool is not None:
        return [{k: v for k, v in t.items() if k not in ("window_start",)} for t in await active(pool, goal)]
    import asyncpg
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    conn = await asyncpg.connect(os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or "", timeout=30)
    try:
        return await status(goal, conn)
    finally:
        await conn.close()
