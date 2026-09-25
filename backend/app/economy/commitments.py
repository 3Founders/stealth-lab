"""Credit commitments to Goals -- the escrow bounty (migration 119).

  commit      a contributor locks Credits on a Goal they can see (append-only
              negative ledger row; the balance can never go below zero).
  withdraw    before the Goal is resolved, the committer takes them back (release).
  settle      when the Goal is resolved (goals.resolved_at -- set only by the
              verification rule), each open commitment is PAID to the contributor
              of the verifying Procedure, or released back to the committer when
              that would be self-dealing or the Procedure has no human contributor.
              Exactly once per commitment (unique index), idempotent on replay.

Demand (a community signal, never truth): per Goal, the open commitments on the
Goal itself (`direct`) and on the Goal or any accepted descendant (`aggregated`),
each commitment counted ONCE per Goal however many paths lead to it (diamonds).
The ranking signal is quadratic -- sum over supporters of sqrt(their Credits) -- so
one large balance cannot dominate. Private Goals the viewer cannot see contribute
nothing, and committer identities are never exposed (only the caller's own).

Nothing here decides resolution, verification, applicability or Procedure ranking.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from app.services.access import AccessScope, visibility_predicate

MIN_COMMITMENT = 1
MAX_AGGREGATION_DEPTH = 16


class CommitmentError(ValueError):
    """A commitment request the ledger rules refuse (a 4xx for the API)."""


class GoalNotCommittable(CommitmentError):
    pass


class InsufficientCredits(CommitmentError):
    pass


async def _visible_goal(pool: Any, goal_id: str, access_scope: AccessScope) -> Optional[dict]:
    vis, params = visibility_predicate(access_scope, alias="g", param_index=2)
    row = await pool.fetchrow(
        f"SELECT goal_id::text AS id, status FROM goal_search_index g "
        f"WHERE g.goal_id = $1::uuid AND g.status IN ('active', 'candidate') AND {vis}",
        str(goal_id), *params)
    return dict(row) if row else None


async def commit(
    pool: Any, *, goal_id: str, contributor_id: str, credits: int, idempotency_key: str,
    access_scope: AccessScope,
) -> dict[str, Any]:
    if int(credits) < MIN_COMMITMENT:
        raise CommitmentError(f"commit at least {MIN_COMMITMENT} Credit")
    if not idempotency_key or len(idempotency_key) > 200:
        raise CommitmentError("an idempotency_key (1-200 chars) is required")
    goal = await _visible_goal(pool, goal_id, access_scope)
    if goal is None:
        raise GoalNotCommittable("goal not found")
    # resolution is read from the CANONICAL row (goals.resolved_at is the one
    # authoritative signal; the projection copy may lag a moment behind)
    from app.services.routed_reads import fetch_goal

    canonical = await fetch_goal(pool, str(goal_id), columns="resolved_at")
    if canonical is None:
        raise GoalNotCommittable("goal not found")
    if canonical["resolved_at"] is not None:
        raise GoalNotCommittable("this Goal is already resolved")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # the same per-contributor lock every Credit writer takes (economy/credits.py)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"credits:contributor:{contributor_id}")
            existing = await conn.fetchrow(
                "SELECT id::text AS id, goal_id::text AS goal_id, -amount AS credits FROM credit_ledger_events "
                "WHERE reason = 'goal_commitment' AND contributor_id = $1 AND metadata ->> 'idempotency_key' = $2",
                contributor_id, idempotency_key)
            if existing is not None:
                return {**dict(existing), "created": False}
            balance = float(await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM credit_ledger_events WHERE contributor_id = $1",
                contributor_id))
            if balance < int(credits):
                raise InsufficientCredits(f"available balance is {balance:g} Credits")
            row = await conn.fetchrow(
                "INSERT INTO credit_ledger_events (contributor_id, amount, reason, goal_id, metadata, created_by) "
                "VALUES ($1, $2, 'goal_commitment', $3::uuid, $4::jsonb, 'goal_commitments') "
                "RETURNING id::text AS id, goal_id::text AS goal_id, -amount AS credits",
                contributor_id, -int(credits), str(goal_id), {"idempotency_key": idempotency_key})
    return {**dict(row), "created": True}


async def withdraw(pool: Any, *, commitment_id: str, contributor_id: str) -> dict[str, Any]:
    row = await pool.fetchrow(
        "SELECT id::text AS id, contributor_id, goal_id::text AS goal_id, credits, settlement "
        "FROM goal_commitments WHERE id = $1::uuid", commitment_id)
    if row is None or row["contributor_id"] != contributor_id:
        raise CommitmentError("commitment not found")
    if row["settlement"] is not None:
        raise CommitmentError(f"this commitment is already settled ({row['settlement']})")
    released = await pool.fetchrow(
        "INSERT INTO credit_ledger_events (contributor_id, amount, reason, goal_id, reversal_of_event_id, metadata, created_by) "
        "VALUES ($1, $2, 'goal_commitment_release', $3::uuid, $4::uuid, $5::jsonb, 'goal_commitments') "
        "ON CONFLICT (reversal_of_event_id) WHERE reason IN ('goal_commitment_release', 'bounty_payout') DO NOTHING "
        "RETURNING id::text AS id",
        contributor_id, row["credits"], row["goal_id"], row["id"], {"why": "withdrawn"})
    if released is None:
        raise CommitmentError("this commitment is already settled")
    return {"commitment_id": row["id"], "released": float(row["credits"])}


async def _procedure_contributor(pool: Any, procedure_row_id: Optional[str], procedure_id: Optional[str]) -> Optional[str]:
    """The contributor of the verifying Procedure: the submitter of the accepted
    submission for that exact version, else for another version of it."""
    if not procedure_row_id and not procedure_id:
        return None
    rows = [procedure_row_id] if procedure_row_id else []
    if procedure_id:
        rows += [r[0] for r in await pool.fetch(
            "SELECT row_id::text FROM procedure_row_routes WHERE procedure_id = $1::uuid", procedure_id)]
    if not rows:
        return None
    return await pool.fetchval(
        "SELECT submitted_by FROM procedure_submissions WHERE status = 'accepted' "
        "AND procedure_row_id = ANY($1::uuid[]) "
        "ORDER BY (procedure_row_id = $2::uuid) DESC, (submission_type = 'new') DESC, created_at LIMIT 1",
        rows, procedure_row_id or rows[0])


async def settle_goal_bounties(
    pool: Any, goal_id: str, *, procedure_row_id: Optional[str] = None, procedure_id: Optional[str] = None,
) -> dict[str, Any]:
    """Called AFTER goals.resolved_at is set. Pays every open commitment on the Goal
    to the verifying Procedure's contributor (release on self-dealing / no
    contributor). Idempotent: a settled commitment is never settled again."""
    solver = await _procedure_contributor(pool, procedure_row_id, procedure_id)
    open_rows = await pool.fetch(
        "SELECT id::text AS id, contributor_id, credits FROM goal_commitments "
        "WHERE goal_id = $1::uuid AND settlement IS NULL ORDER BY created_at, id", str(goal_id))
    out = {"paid": 0, "released": 0, "credits_paid": 0.0, "solver_known": solver is not None}
    for row in open_rows:
        pay = solver is not None and solver != row["contributor_id"]
        why = "resolved" if pay else ("self_dealing" if solver is not None else "no_contributor")
        inserted = await pool.fetchval(
            "INSERT INTO credit_ledger_events (contributor_id, amount, reason, goal_id, reversal_of_event_id, "
            "procedure_row_id, procedure_id, metadata, created_by) "
            "VALUES ($1, $2, $3, $4::uuid, $5::uuid, $6::uuid, $7::uuid, $8::jsonb, 'goal_commitments') "
            "ON CONFLICT (reversal_of_event_id) WHERE reason IN ('goal_commitment_release', 'bounty_payout') DO NOTHING "
            "RETURNING id",
            solver if pay else row["contributor_id"], row["credits"],
            "bounty_payout" if pay else "goal_commitment_release", str(goal_id), row["id"],
            procedure_row_id if pay else None, procedure_id if pay else None, {"why": why})
        if inserted is not None:
            if pay:
                out["paid"] += 1
                out["credits_paid"] += float(row["credits"])
            else:
                out["released"] += 1
    return out


def _quadratic(per_supporter: dict[str, float]) -> float:
    return sum(math.sqrt(credits) for credits in per_supporter.values() if credits > 0)


async def goal_demand(pool: Any, goal_ids: Sequence[str], *, access_scope: AccessScope) -> dict[str, dict[str, Any]]:
    """{goal_id: {"direct": {...}, "aggregated": {...}}} from OPEN commitments.
    `aggregated` covers the Goal and its accepted descendants the viewer can see;
    every commitment counts once per Goal. Totals only -- no committer identities."""
    ids = list(dict.fromkeys(str(g) for g in goal_ids if g))
    if not ids:
        return {}
    vis, params = visibility_predicate(access_scope, alias="d", param_index=3)
    rows = await pool.fetch(
        f"""
        WITH RECURSIVE reach(root_id, goal_id, depth) AS (
            SELECT g, g, 0 FROM unnest($1::uuid[]) AS g
            UNION
            SELECT reach.root_id, r.specific_goal_id, reach.depth + 1
              FROM reach
              JOIN goal_relations r ON r.abstract_goal_id = reach.goal_id
               AND r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
             WHERE reach.depth < $2
        )
        SELECT DISTINCT reach.root_id::text AS root_id, (reach.goal_id = reach.root_id) AS direct,
               c.id::text AS commitment_id, c.contributor_id, c.credits
          FROM reach
          JOIN goal_search_index d ON d.goal_id = reach.goal_id
          JOIN goal_commitments c ON c.goal_id = reach.goal_id AND c.settlement IS NULL
         WHERE {vis}
        """,
        ids, MAX_AGGREGATION_DEPTH, *params)
    result: dict[str, dict[str, Any]] = {}
    for goal_id in ids:
        seen: set[str] = set()
        direct: dict[str, float] = {}
        aggregated: dict[str, float] = {}
        for row in rows:
            if row["root_id"] != goal_id or row["commitment_id"] in seen:
                continue
            seen.add(row["commitment_id"])
            credits = float(row["credits"])
            aggregated[row["contributor_id"]] = aggregated.get(row["contributor_id"], 0.0) + credits
            if row["direct"]:
                direct[row["contributor_id"]] = direct.get(row["contributor_id"], 0.0) + credits
        result[goal_id] = {
            "direct": {"committed_credits": sum(direct.values()), "supporters": len(direct),
                       "demand_score": round(_quadratic(direct), 4)},
            "aggregated": {"committed_credits": sum(aggregated.values()), "supporters": len(aggregated),
                           "demand_score": round(_quadratic(aggregated), 4)},
        }
    return result


async def my_commitments(pool: Any, *, goal_id: str, contributor_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT id::text AS id, credits, created_at, settlement, settled_at FROM goal_commitments "
        "WHERE goal_id = $1::uuid AND contributor_id = $2 ORDER BY created_at DESC", str(goal_id), contributor_id)
    return [dict(row) for row in rows]


def ranking_inputs(demand: dict[str, Any]) -> dict[str, Any]:
    """The fields goal_ranking.demand_factor_from_commitments reads: the quadratic
    aggregated demand and the supporter count (never raw balances)."""
    aggregated = (demand or {}).get("aggregated") or {}
    if not aggregated.get("supporters"):
        return {}
    return {"committed_credit_count": aggregated["demand_score"], "supporter_count": aggregated["supporters"]}
