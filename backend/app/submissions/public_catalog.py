"""The published-Ways catalogue: the public list behind the homepage and the task search.

Kept out of `store.py` so the catalogue query does not ride on the submission-store module's
uncommitted work. Only approved/ai_approved Ways; the owner, the reviewer and the internal goal
ids are never in the projection. The exact version's price comes from its parent procedure.
"""
from __future__ import annotations

from typing import Any


def _plain(row: dict) -> dict:
    for key, value in list(row.items()):
        if hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    return row


async def list_public(pool: Any, *, q: str = "", limit: int = 12, offset: int = 0) -> list[dict]:
    term = (q or "").strip()
    rows = await pool.fetch(
        """SELECT g.id::text AS id, g.goal_name, g.description, g.mode, g.status, g.created_at,
                  g.parent_procedure_id::text AS parent_procedure_id,
                  p.price_paise, p.price_currency,
                  count(s.*) AS steps
           FROM goal_submissions g
           LEFT JOIN goal_submission_steps s ON s.submission_id = g.id
           LEFT JOIN procedures p ON p.id = g.parent_procedure_id
           WHERE g.status IN ('approved', 'ai_approved')
             AND ($1 = '' OR g.goal_name ILIKE '%' || $1 || '%' OR g.description ILIKE '%' || $1 || '%')
           GROUP BY g.id, p.price_paise, p.price_currency
           ORDER BY g.created_at DESC, g.id DESC LIMIT $2 OFFSET $3""",
        term, int(limit), max(0, int(offset)))
    return [_plain(dict(r)) for r in rows]
