"""Reviews on a Way: one person's verdict, kept apart from the measured evidence.

A review is opinion. It never feeds a capability number, an applicability decision or a ranking of
Ways — those come from recorded runs (app/execution/evidence.py). The table is separate for exactly
that reason: a five-star review must not be able to move a success rate.

One review per person per Way. Posting again edits the existing row rather than adding a second, so
a Way cannot be inflated by repetition.

Only a published Way can be reviewed, and only a signed-in person can review. Both are checked here
rather than in the route, so the MCP tool and the website get the same answer.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

MIN_BODY = 3
MAX_BODY = 4000
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


class ReviewError(ValueError):
    """A review that cannot be stored, with the sentence to show the person."""


def _author_uuid(value: Any) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _clean(rating: Any, body: Any) -> tuple[int, str]:
    try:
        score = int(rating)
    except (TypeError, ValueError):
        raise ReviewError("give a rating from 1 to 5")
    if score < 1 or score > 5:
        raise ReviewError("give a rating from 1 to 5")
    text = str(body or "").strip()
    if len(text) < MIN_BODY:
        raise ReviewError("write a sentence or two about what the output was like")
    return score, text[:MAX_BODY]


def _row(row: Any) -> dict[str, Any]:
    """A review as JSON: datetimes become ISO strings, because the route serialises with no default."""
    item = dict(row)
    created = item.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        item["created_at"] = created.isoformat()
    return item


async def list_for(pool: Any, submission_id: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """The visible reviews for one Way, newest first, with the average and the count.

    The average is computed over every visible review, not only the page returned, so a Way with
    more reviews than one page still reports its real average.
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    rows = await pool.fetch(
        """SELECT id::text AS id, author_name, rating, body, created_at,
                  COUNT(*) OVER () AS total, AVG(rating) OVER () AS average
             FROM way_reviews
            WHERE submission_id::text = $1 AND hidden = FALSE
            ORDER BY created_at DESC
            LIMIT $2""",
        str(submission_id), limit)
    items = [_row(r) for r in rows]
    total = int(items[0]["total"]) if items else 0
    average = float(items[0]["average"]) if items and items[0].get("average") is not None else None
    for item in items:
        item.pop("total", None)
        item.pop("average", None)
    return {"reviews": items, "count": total, "average": round(average, 2) if average is not None else None}


async def upsert(pool: Any, *, submission_id: str, author_user_id: Any, rating: Any, body: Any,
                 procedure_row_id: Optional[str] = None, source: str = "website") -> dict[str, Any]:
    """Store one person's review of one published Way; a second review edits the first."""
    author = _author_uuid(author_user_id)
    if author is None:
        raise ReviewError("sign in to review a Way")
    try:
        UUID(str(submission_id))
    except (TypeError, ValueError):
        raise LookupError("no such Way")
    score, text = _clean(rating, body)

    way = await pool.fetchrow(
        "SELECT status, parent_procedure_id::text AS parent FROM goal_submissions WHERE id::text = $1",
        str(submission_id))
    if way is None:
        raise LookupError("no such Way")
    if way["status"] not in ("approved", "ai_approved"):
        raise ReviewError("this Way is not published, so it cannot be reviewed yet")
    version_row = str(procedure_row_id) if procedure_row_id else way.get("parent")

    # A display name is copied at write time: a later rename does not rewrite history, and the
    # review never carries an email, a subject id or any other identifier.
    name = await pool.fetchval("SELECT display_name FROM users WHERE id::text = $1", author)
    author_name = (str(name).strip()[:80] if name else "kell user")

    row = await pool.fetchrow(
        """INSERT INTO way_reviews
               (submission_id, procedure_row_id, author_user_id, author_name, rating, body, scope, source)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'public', $7)
           ON CONFLICT (submission_id, author_user_id) DO UPDATE
             SET rating = EXCLUDED.rating,
                 body = EXCLUDED.body,
                 author_name = EXCLUDED.author_name,
                 procedure_row_id = COALESCE(EXCLUDED.procedure_row_id, way_reviews.procedure_row_id),
                 created_at = now()
           RETURNING id::text AS id, author_name, rating, body, created_at""",
        str(submission_id), (str(version_row) if version_row else None),
        author, author_name, score, text, str(source)[:40])
    return _row(row)
