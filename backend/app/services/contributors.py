"""
People layer -- opt-in public contributor profiles (INV-01: private by default).

This module is the ONE place that:
  - reads/writes the `contributor_profiles` row (migration 48), and
  - computes the aggregate contribution counts shown on a public profile and
    the /v1/contributors leaderboard -- always live, straight off existing
    provenance columns (`procedures.owner_id`, `knowledge_nodes.created_by`,
    `publication_records.actor_user_id`). There is deliberately NO stored
    per-actor score: `personal_contributions.py` already refuses to invent
    one, and a leaderboard number that cannot be re-derived from provenance
    is not defensible.

Visibility is enforced here and in the endpoints, NOT through access.py:
`users` / `contributor_profiles` are not tenant/visibility scoped. A profile
row with `visibility != 'public'` is invisible to everyone except its owner
via /v1/me/profile. A missing or non-public profile is reported as 404 by the
public endpoints -- it never confirms that an account exists.

Own-profile writes are audited by the caller (app/api/profile.py) through
services/audit.py::record_audit_event -- this module does not open a second
audit path.

`executor` is any asyncpg pool or connection (duck-typed `.fetch`/`.fetchrow`),
matching personal_contributions.py.
"""
from __future__ import annotations

from typing import Any, Optional

PUBLIC = "public"
PRIVATE = "private"
_VALID_VISIBILITY = (PRIVATE, PUBLIC)

# Metrics the leaderboard can rank by -- each is a key of contribution_counts().
LEADERBOARD_METRICS = (
    "verified_procedures",
    "procedures_authored",
    "claims_authored",
    "commons_publications",
)
_DEFAULT_METRIC = "verified_procedures"

_TAGLINE_MAX = 280


# --- profile row ---------------------------------------------------------


async def get_profile(executor: Any, user_id: str) -> Optional[dict]:
    row = await executor.fetchrow(
        """
        SELECT user_id, visibility, disclosed_at, tagline, t_created, t_updated
        FROM contributor_profiles WHERE user_id = $1
        """,
        user_id,
    )
    return dict(row) if row else None


async def upsert_profile(
    executor: Any,
    user_id: str,
    *,
    visibility: str,
    tagline: Optional[str] = None,
    mark_disclosed: bool = True,
) -> dict:
    """Create or update the caller's own profile row.

    `mark_disclosed=True` stamps `disclosed_at = now()` -- every disclosure
    submission does this, whichever visibility the person chose, so "was the
    person informed before their name could be listed" is always answerable.
    """
    if visibility not in _VALID_VISIBILITY:
        raise ValueError(f"visibility must be one of {_VALID_VISIBILITY}")
    clean_tagline = (tagline or "").strip()[:_TAGLINE_MAX] or None
    row = await executor.fetchrow(
        """
        INSERT INTO contributor_profiles (user_id, visibility, tagline, disclosed_at)
        VALUES ($1, $2, $3, CASE WHEN $4 THEN now() ELSE NULL END)
        ON CONFLICT (user_id) DO UPDATE SET
            visibility   = EXCLUDED.visibility,
            tagline      = EXCLUDED.tagline,
            disclosed_at = CASE WHEN $4 THEN now()
                                ELSE contributor_profiles.disclosed_at END,
            t_updated    = now()
        RETURNING user_id, visibility, disclosed_at, tagline, t_created, t_updated
        """,
        user_id, visibility, clean_tagline, mark_disclosed,
    )
    return dict(row)


async def delete_profile(executor: Any, user_id: str) -> None:
    """Retract the public listing entirely (used by the data-rights path)."""
    await executor.execute(
        "DELETE FROM contributor_profiles WHERE user_id = $1", user_id
    )


# --- contribution counts (live, off provenance) -------------------------


async def _subject_for(executor: Any, user_id: str) -> Optional[str]:
    row = await executor.fetchrow(
        "SELECT external_subject FROM users WHERE id = $1", user_id
    )
    return row["external_subject"] if row else None


async def contribution_counts(
    executor: Any, *, user_id: str, subject: Optional[str] = None
) -> dict:
    """Aggregate counts for one contributor. No stored score -- recomputed
    every call from provenance columns:

      procedures_authored  -- procedures they own (procedures.owner_id),
                              current version only (t_invalid IS NULL).
      verified_procedures  -- of those, verification_state = 'verified'.
      claims_authored      -- knowledge_nodes claim rows they created.
      commons_publications -- publication_records naming them as actor that
                              have not been withdrawn (the durable data-flow
                              spec Sec 24 contribution record).
    """
    if subject is None:
        subject = await _subject_for(executor, user_id)

    counts = {
        "procedures_authored": 0,
        "verified_procedures": 0,
        "claims_authored": 0,
        "commons_publications": 0,
    }

    if subject is not None:
        prow = await executor.fetchrow(
            """
            SELECT
              count(*)                                                 AS authored,
              count(*) FILTER (WHERE verification_state = 'verified')   AS verified
            FROM procedures
            WHERE owner_id = $1 AND t_invalid IS NULL
            """,
            subject,
        )
        if prow is not None:
            counts["procedures_authored"] = int(prow["authored"] or 0)
            counts["verified_procedures"] = int(prow["verified"] or 0)

        crow = await executor.fetchrow(
            """
            SELECT count(*) AS n FROM knowledge_nodes
            WHERE created_by = $1 AND node_type = 'claim' AND t_invalid IS NULL
            """,
            subject,
        )
        if crow is not None:
            counts["claims_authored"] = int(crow["n"] or 0)

    pubrow = await executor.fetchrow(
        """
        SELECT count(*) AS n FROM publication_records
        WHERE actor_user_id = $1
          AND (withdrawal_state IS NULL
               OR withdrawal_state = 'retained_as_independently_sourced')
        """,
        user_id,
    )
    if pubrow is not None:
        counts["commons_publications"] = int(pubrow["n"] or 0)

    return counts


# --- public read surface ----------------------------------------------


async def public_profile(executor: Any, user_id: str) -> Optional[dict]:
    """The public view of one contributor, or None when they have not opted
    in (caller turns None into a 404 that does not confirm the account)."""
    row = await executor.fetchrow(
        """
        SELECT u.id AS user_id, u.display_name, p.tagline, p.t_created AS profile_since
        FROM contributor_profiles p JOIN users u ON u.id = p.user_id
        WHERE p.user_id = $1 AND p.visibility = 'public' AND u.is_active
        """,
        user_id,
    )
    if row is None:
        return None
    counts = await contribution_counts(executor, user_id=str(row["user_id"]))
    return {
        "user_id": str(row["user_id"]),
        "display_name": row["display_name"] or "Contributor",
        "tagline": row["tagline"],
        "profile_since": row["profile_since"],
        "counts": counts,
    }


async def search_public(executor: Any, q: str, *, limit: int = 20) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    rows = await executor.fetch(
        """
        SELECT u.id AS user_id, u.display_name, p.tagline
        FROM contributor_profiles p JOIN users u ON u.id = p.user_id
        WHERE p.visibility = 'public' AND u.is_active
          AND u.display_name ILIKE '%' || $1 || '%'
        ORDER BY u.display_name ASC
        LIMIT $2
        """,
        q, limit,
    )
    return [
        {
            "user_id": str(r["user_id"]),
            "display_name": r["display_name"] or "Contributor",
            "tagline": r["tagline"],
        }
        for r in rows
    ]


async def leaderboard(
    executor: Any, *, metric: str = _DEFAULT_METRIC, limit: int = 25
) -> dict:
    """Rank opted-in contributors by one metric.

    Counts are recomputed per contributor (a few small aggregates each) --
    fine at launch scale and it keeps every number re-derivable from
    provenance. Revisit with a single grouped query if the public cohort
    grows large.
    """
    if metric not in LEADERBOARD_METRICS:
        raise ValueError(f"metric must be one of {LEADERBOARD_METRICS}")
    limit = max(1, min(int(limit), 100))

    rows = await executor.fetch(
        """
        SELECT u.id AS user_id, u.display_name, u.external_subject, p.tagline
        FROM contributor_profiles p JOIN users u ON u.id = p.user_id
        WHERE p.visibility = 'public' AND u.is_active
        """
    )

    ranked: list[dict] = []
    for r in rows:
        counts = await contribution_counts(
            executor, user_id=str(r["user_id"]), subject=r["external_subject"]
        )
        ranked.append(
            {
                "user_id": str(r["user_id"]),
                "display_name": r["display_name"] or "Contributor",
                "tagline": r["tagline"],
                "counts": counts,
                "value": counts[metric],
            }
        )

    ranked.sort(key=lambda e: (-e["value"], e["display_name"].lower()))
    return {"metric": metric, "entries": ranked[:limit]}
