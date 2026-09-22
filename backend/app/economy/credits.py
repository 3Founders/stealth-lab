"""
The keळ Credits ledger (§7, §8, §9): append-only, reconstructable balance,
no mutable balance column, no cash-out. See migration 102's header comment
for why this is the one deliberate departure from
contributor_profiles' "no stored per-actor score" philosophy.

HARDENING PASS (audit findings D4/D5/D18): reward issuance is now atomic
under concurrency. Every cap check + insert happens inside one explicit
transaction, serialized per-recipient (and, for procedure-scoped caps,
per-procedure) via `pg_advisory_xact_lock` -- a session/transaction-scoped
Postgres lock keyed by `hashtext(key)`, released automatically on
commit/rollback. This is the simplest mechanism that actually closes the
race the audit described (20 concurrent requests, cap=5 -> at most 5 paid):
a second concurrent request for the same contributor blocks on the lock
until the first commits, then re-reads the ledger and sees the first
request's row already counted. Lock collisions across different
contributor_id strings are possible in principle (hashtext is 32-bit) but
astronomically unlikely at V1 scale, and a false serialization only costs
latency, never correctness -- an acceptable, explicitly simplest-available
tradeoff (no new lock table, no new columns).

Idempotency (migration 103's unique indexes) is enforced via
`ON CONFLICT ... DO NOTHING` + a fallback read: a retried or duplicated
reward attempt for the same (submission, reason) or (usage_event, reason,
contributor) returns the ALREADY-RECORDED event rather than creating a
second one or raising.

Every reward path funnels through `_record`/`_capped_reward` so the caps
and the never-reward list (views, copies, self-use, claimed success,
duplicates) are enforced in exactly one place.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from app.economy import constants as c
from app.utils.ids import uuid7

REWARD_REASONS = ("new_procedure", "improvement", "verified_reuse")


async def get_balance(pool: asyncpg.Pool, contributor_id: str) -> float:
    row = await pool.fetchrow("SELECT balance FROM credit_balances WHERE contributor_id = $1", contributor_id)
    return float(row["balance"]) if row else 0.0


async def get_history(pool: asyncpg.Pool, contributor_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM credit_ledger_events WHERE contributor_id = $1 ORDER BY created_at DESC LIMIT $2",
        contributor_id, limit,
    )
    return [dict(r) for r in rows]


async def _rewards_paid_since(conn: Any, contributor_id: str, *, hours: int) -> float:
    """`conn` is an asyncpg Pool or an active Connection (duck-typed, same
    convention app/services/contributors.py already uses) -- callers that
    need the read inside a locked transaction pass the Connection so it
    sees the transaction's own writes-in-progress consistently."""
    val = await conn.fetchval(
        """
        SELECT COALESCE(SUM(amount), 0) FROM credit_ledger_events
        WHERE contributor_id = $1 AND reason = ANY($2::text[])
          AND created_at >= now() - ($3 || ' hours')::interval
        """,
        contributor_id, list(REWARD_REASONS), str(hours),
    )
    return float(val or 0)


async def _reuse_rewards_paid_today(conn: Any, procedure_row_id: str) -> int:
    val = await conn.fetchval(
        """
        SELECT count(*) FROM credit_ledger_events
        WHERE procedure_row_id = $1 AND reason = 'verified_reuse'
          AND created_at >= now() - interval '24 hours'
        """,
        procedure_row_id,
    )
    return int(val or 0)


def _conflict_clause(*, submission_id: Optional[str], usage_event_id: Optional[str]) -> str:
    """Matches migration 103's two partial unique indexes exactly -- the
    ON CONFLICT target must repeat the same predicate as the index."""
    if submission_id is not None:
        return "ON CONFLICT (submission_id, reason) WHERE submission_id IS NOT NULL DO NOTHING"
    if usage_event_id is not None:
        return "ON CONFLICT (usage_event_id, reason, contributor_id) WHERE usage_event_id IS NOT NULL DO NOTHING"
    return ""


async def _existing_reward(
    conn: Any, *, contributor_id: str, reason: str, submission_id: Optional[str], usage_event_id: Optional[str],
) -> Optional[dict[str, Any]]:
    if submission_id is not None:
        row = await conn.fetchrow(
            "SELECT * FROM credit_ledger_events WHERE submission_id = $1 AND reason = $2", submission_id, reason,
        )
    elif usage_event_id is not None:
        row = await conn.fetchrow(
            "SELECT * FROM credit_ledger_events WHERE usage_event_id = $1 AND reason = $2 AND contributor_id = $3",
            usage_event_id, reason, contributor_id,
        )
    else:
        row = None
    return dict(row) if row else None


async def _record(
    conn: Any, *, contributor_id: str, amount: float, reason: str,
    goal_id: Optional[str] = None, procedure_row_id: Optional[str] = None, procedure_id: Optional[str] = None,
    usage_event_id: Optional[str] = None, submission_id: Optional[str] = None,
    reversal_of_event_id: Optional[str] = None, created_by: str = "economy_service",
    metadata: Optional[dict] = None,
) -> Optional[dict[str, Any]]:
    """
    Idempotent insert: on a conflict with migration 103's unique indexes
    (same submission+reason, or same usage_event+reason+contributor),
    returns the EXISTING row instead of creating a duplicate. `conn` must
    be an active connection when the caller needs this inside a locked
    transaction (see `_capped_reward`); a bare Pool is fine for one-shot
    single-statement writes (e.g. `clawback`).
    """
    row_id = str(uuid7())
    conflict = _conflict_clause(submission_id=submission_id, usage_event_id=usage_event_id)
    row = await conn.fetchrow(
        f"""
        INSERT INTO credit_ledger_events (
            id, contributor_id, amount, reason, goal_id, procedure_row_id, procedure_id,
            usage_event_id, submission_id, reversal_of_event_id, created_by, metadata
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
        {conflict}
        RETURNING *
        """,  # noqa: S608 -- `conflict` is one of two fixed literals from _conflict_clause, never interpolated input
        row_id, contributor_id, amount, reason, goal_id, procedure_row_id, procedure_id,
        usage_event_id, submission_id, reversal_of_event_id, created_by, json.dumps(metadata or {}),
    )
    if row is not None:
        return dict(row)
    if conflict:
        return await _existing_reward(conn, contributor_id=contributor_id, reason=reason, submission_id=submission_id, usage_event_id=usage_event_id)
    return None


async def _capped_reward(
    pool: asyncpg.Pool, *, contributor_id: str, amount: float, reason: str, **kw,
) -> Optional[dict[str, Any]]:
    """
    Applies the daily/weekly cap atomically (§10): locks per-contributor
    (`pg_advisory_xact_lock`), re-reads the ledger inside that lock, then
    inserts. Returns None (never a partial silent reward) if nothing is
    left under the cap, or the existing row if this exact reward was
    already recorded (idempotent retry).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"credits:contributor:{contributor_id}")
            paid_today = await _rewards_paid_since(conn, contributor_id, hours=24)
            paid_this_week = await _rewards_paid_since(conn, contributor_id, hours=24 * 7)
            remaining_daily = c.DAILY_REWARD_CAP_PER_CONTRIBUTOR - paid_today
            remaining_weekly = c.WEEKLY_REWARD_CAP_PER_CONTRIBUTOR - paid_this_week
            capped = min(amount, remaining_daily, remaining_weekly)
            if capped <= 0:
                return None
            metadata = dict(kw.pop("metadata", None) or {})
            if capped < amount:
                metadata["capped_from"] = amount
            return await _record(conn, contributor_id=contributor_id, amount=capped, reason=reason, metadata=metadata, **kw)


async def reward_new_procedure(pool: asyncpg.Pool, *, submission: dict[str, Any]) -> Optional[dict[str, Any]]:
    return await _capped_reward(
        pool, contributor_id=submission["submitted_by"], amount=c.NEW_PROCEDURE_REWARD, reason="new_procedure",
        goal_id=str(submission["goal_id"]), procedure_row_id=str(submission["procedure_row_id"]) if submission.get("procedure_row_id") else None,
        submission_id=str(submission["id"]),
    )


async def reward_improvement(pool: asyncpg.Pool, *, submission: dict[str, Any]) -> Optional[dict[str, Any]]:
    return await _capped_reward(
        pool, contributor_id=submission["submitted_by"], amount=c.IMPROVEMENT_REWARD, reason="improvement",
        goal_id=str(submission["goal_id"]), procedure_row_id=str(submission["procedure_row_id"]) if submission.get("procedure_row_id") else None,
        submission_id=str(submission["id"]),
    )


async def reward_verified_reuse(pool: asyncpg.Pool, *, usage_event: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Independent verified reuse is the main recurring reward (§7, §9).
    Never rewards self-use. Attribution is single-hop only: the current
    Procedure's contributor, plus (if this Procedure came from an
    'improvement' submission) its immediate parent's contributor at a
    reduced share -- no deeper chain, no Shapley value.

    The per-procedure daily cap and every recipient's per-contributor caps
    are enforced inside ONE transaction, locked in a stable order
    (procedure, then contributors sorted) so concurrent reuse events can
    never deadlock each other and can never jointly exceed either cap.
    """
    if usage_event.get("is_self_use"):
        return []
    if usage_event.get("outcome_state") != "verified_success":
        return []
    procedure_row_id = str(usage_event["procedure_row_id"])
    primary_contributor = usage_event["contributor_id"]

    # LINEAGE FIX: the submission that created this Procedure's FAMILY may
    # have captured a row that was later superseded (app.services.
    # procedures.supersede_procedure creates a NEW row, same procedure_id,
    # for the next version, and tombstones the old one). Looking up
    # `procedure_submissions.procedure_row_id = $1` directly would silently
    # miss the submission -- and therefore drop parent attribution -- the
    # moment the reused row is a newer version than the one the submission
    # recorded. Resolve by the STABLE `procedure_id` (shared across every
    # version) instead: find the submission whose OWN captured row shares
    # this row's `procedure_id`, regardless of which specific version row
    # either one is. Tombstoned rows are never deleted (bitemporal
    # tombstone, not a hard delete), so this join is safe across any number
    # of supersessions.
    parent_contributor = await pool.fetchval(
        """
        SELECT parent_p.created_by
        FROM procedure_submissions sub
        JOIN procedures sub_p ON sub_p.id = sub.procedure_row_id
        JOIN procedures used_p ON used_p.id = $1
        JOIN procedures parent_p ON parent_p.id = sub.parent_procedure_row_id
        WHERE sub.submission_type = 'improvement'
          AND sub_p.procedure_id = used_p.procedure_id
        ORDER BY sub.created_at ASC
        LIMIT 1
        """,
        procedure_row_id,
    )
    recipients = {primary_contributor}
    if parent_contributor and parent_contributor != primary_contributor:
        recipients.add(parent_contributor)

    events: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"credits:procedure:{procedure_row_id}")
            if await _reuse_rewards_paid_today(conn, procedure_row_id) >= c.DAILY_VERIFIED_REUSE_REWARDS_PER_PROCEDURE:
                return []

            for contributor_id in sorted(recipients):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"credits:contributor:{contributor_id}")
                is_parent = contributor_id != primary_contributor
                amount = c.VERIFIED_REUSE_REWARD * (c.PARENT_ATTRIBUTION_SHARE if is_parent else 1.0)

                paid_today = await _rewards_paid_since(conn, contributor_id, hours=24)
                paid_this_week = await _rewards_paid_since(conn, contributor_id, hours=24 * 7)
                capped = min(amount, c.DAILY_REWARD_CAP_PER_CONTRIBUTOR - paid_today, c.WEEKLY_REWARD_CAP_PER_CONTRIBUTOR - paid_this_week)
                if capped <= 0:
                    continue

                metadata: dict[str, Any] = {"parent_attribution": True} if is_parent else {}
                if capped < amount:
                    metadata["capped_from"] = amount

                reward = await _record(
                    conn, contributor_id=contributor_id, amount=capped, reason="verified_reuse",
                    goal_id=str(usage_event["goal_id"]) if usage_event.get("goal_id") else None,
                    procedure_row_id=procedure_row_id, procedure_id=str(usage_event["procedure_id"]),
                    usage_event_id=str(usage_event["id"]), metadata=metadata,
                )
                if reward:
                    events.append(reward)
    return events


async def clawback(pool: asyncpg.Pool, *, event_id: str, reason_text: str, created_by: str) -> dict[str, Any]:
    """Idempotent: a second clawback attempt on the same event_id returns
    the existing reversal (migration 103's uq_credit_ledger_events_reversal_of)
    rather than creating a second one."""
    original = await pool.fetchrow("SELECT * FROM credit_ledger_events WHERE id = $1", event_id)
    if original is None:
        raise ValueError(f"credit ledger event {event_id} not found")
    if original["reason"] == "clawback":
        raise ValueError("cannot claw back a clawback row")

    existing = await pool.fetchrow("SELECT * FROM credit_ledger_events WHERE reversal_of_event_id = $1", event_id)
    if existing is not None:
        return dict(existing)

    row_id = str(uuid7())
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO credit_ledger_events (
                id, contributor_id, amount, reason, goal_id, procedure_row_id, procedure_id,
                usage_event_id, submission_id, reversal_of_event_id, created_by, metadata
            ) VALUES ($1,$2,$3,'clawback',$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
            RETURNING *
            """,
            row_id, original["contributor_id"], -float(original["amount"]),
            original["goal_id"], original["procedure_row_id"], original["procedure_id"],
            original["usage_event_id"], original["submission_id"], event_id, created_by,
            json.dumps({"reason_text": reason_text}),
        )
    except asyncpg.exceptions.UniqueViolationError:
        existing = await pool.fetchrow("SELECT * FROM credit_ledger_events WHERE reversal_of_event_id = $1", event_id)
        if existing is None:
            raise
        return dict(existing)
    return dict(row)
