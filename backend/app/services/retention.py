"""Retention for OPERATIONAL rows in the control database.

The control database holds routing, projections, the Goal hierarchy and the
economy -- and, with a hosted storage limit, it is the first database to fill up.
Some of its tables are pure operations logs that grow without bound. This prunes
only those, only past an explicit age, and only when asked to (`apply=False`
counts). It never touches canonical knowledge, relations, the Credit ledger,
identity decisions (audit trail of every identity judgment) or anything pending.
"""
from __future__ import annotations

from typing import Any

# (table, age column, extra condition) -- finished work only
_PRUNABLE = (
    ("retrieval_decisions", "created_at", "TRUE"),
    ("projection_outbox", "applied_at", "status = 'applied'"),
    ("ingestion_jobs", "completed_at", "status IN ('done', 'cancelled')"),
)


async def prune_operational_rows(pool: Any, *, older_than_days: int, apply: bool = False) -> dict[str, Any]:
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    report: dict[str, Any] = {"older_than_days": older_than_days, "applied": apply, "tables": {}}
    for table, column, condition in _PRUNABLE:
        where = f"{column} < now() - make_interval(days => $1) AND {condition}"
        if apply:
            tag = await pool.execute(f"DELETE FROM {table} WHERE {where}", older_than_days)
            report["tables"][table] = int(str(tag).split()[-1])
        else:
            report["tables"][table] = int(await pool.fetchval(f"SELECT count(*) FROM {table} WHERE {where}", older_than_days))
    return report
