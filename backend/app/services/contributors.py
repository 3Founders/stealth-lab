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

import asyncpg

from app.services import username_generator as ug
from app.services.username_generator import InvalidUsername  # noqa: F401  (re-exported for callers)

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


_PROFILE_COLS = (
    "user_id, visibility, disclosed_at, tagline, username, avatar_locator, "
    "onboarding_complete, t_created, t_updated"
)


async def get_profile(executor: Any, user_id: str) -> Optional[dict]:
    row = await executor.fetchrow(
        f"""
        SELECT {_PROFILE_COLS}
        FROM contributor_profiles WHERE user_id = $1
        """,  # noqa: S608 -- _PROFILE_COLS is a fixed module constant, never interpolated input
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
        RETURNING {_PROFILE_COLS}
        """,  # noqa: S608 -- _PROFILE_COLS is a fixed module constant, never interpolated input
        user_id, visibility, clean_tagline, mark_disclosed,
    )
    return dict(row)


# --- username (V1 contributor identity, migration 106) -------------------
#
# The public keळ username. NOT authentication, NOT users.display_name --
# see migration 106's header. Generation is race-safe: candidates are tried
# against the DB one at a time via INSERT ... ON CONFLICT, so a concurrent
# collision surfaces as UniqueViolationError (from either the plain unique
# index or the reject_reused_username trigger) rather than a duplicate.


async def _try_claim_username(executor: Any, user_id: str, username: str, *, only_if_unset: bool) -> Optional[dict]:
    """One atomic attempt to set contributor_profiles.username = username
    for user_id. `only_if_unset=True` is the lazy-provisioning path (never
    overwrites a username the person already has, including one set by a
    concurrent request that beat us here); False is the explicit rename
    path (always overwrites). Returns the updated row, or None if
    only_if_unset blocked the write (someone else already has a username).
    Raises asyncpg.exceptions.UniqueViolationError on a real collision --
    callers catch it and try the next candidate."""
    guard = "WHERE contributor_profiles.username IS NULL" if only_if_unset else ""
    row = await executor.fetchrow(
        f"""
        INSERT INTO contributor_profiles (user_id, username)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, t_updated = now()
        {guard}
        RETURNING {_PROFILE_COLS}
        """,  # noqa: S608 -- guard is one of two fixed literals, _PROFILE_COLS a fixed constant
        user_id, username,
    )
    return dict(row) if row else None


async def ensure_profile(executor: Any, user_id: str) -> dict:
    """Lazily provision the caller's contributor_profiles row and a
    generated username, if either is missing. Idempotent: a fully-
    provisioned profile is returned unchanged. This is the ONLY place a
    username is auto-generated -- called from GET /v1/me/profile so
    provisioning happens on first real use, without hooking into
    authn.py's ensure_user() (identity acquisition and profile
    provisioning stay deliberately separate, same seam authn.py itself
    documents for identity vs tenancy)."""
    profile = await get_profile(executor, user_id)
    if profile is not None and profile.get("username"):
        return profile

    for cand in ug.generate_candidates(8):
        try:
            claimed = await _try_claim_username(executor, user_id, cand, only_if_unset=True)
        except asyncpg.exceptions.UniqueViolationError:
            continue
        if claimed is not None:
            return claimed

    # Every plain adjective+noun candidate collided (or a concurrent
    # request already won) -- numeric-suffix fallback off one more base.
    base = next(iter(ug.generate_candidates(1)), "Contributor")
    for cand in ug.numeric_suffix_fallback(base):
        try:
            claimed = await _try_claim_username(executor, user_id, cand, only_if_unset=True)
        except asyncpg.exceptions.UniqueViolationError:
            continue
        if claimed is not None:
            return claimed

    # Last resort: someone else's concurrent request may have already
    # provisioned us (only_if_unset made our writes no-ops in that case).
    profile = await get_profile(executor, user_id)
    if profile is not None and profile.get("username"):
        return profile
    raise RuntimeError(f"could not allocate a username for user {user_id} after exhausting candidates")


async def suggest_username(executor: Any, *, limit: int = 1) -> list[str]:
    """Read-only: generate candidate(s) and return the ones NOT already
    taken (active or historical), without reserving anything. Used by the
    onboarding 'Generate another' action -- the frontend never gets to
    claim one of these without going back through rename_username's own
    server-side check."""
    out: list[str] = []
    for cand in ug.generate_candidates(max(limit * 3, 8)):
        if len(out) >= limit:
            break
        taken = await executor.fetchrow(
            "SELECT 1 FROM contributor_profiles WHERE lower(username) = lower($1) "
            "UNION ALL SELECT 1 FROM username_history WHERE lower(old_username) = lower($1) LIMIT 1",
            cand,
        )
        if taken is None:
            out.append(cand)
    return out


async def rename_username(executor: Any, user_id: str, new_username: str) -> dict:
    """Explicit user-initiated rename (account settings / onboarding
    'Keep this name' with edits). Validates, reserves the new name, and
    records the OLD name in username_history so it can never be claimed by
    someone else -- in that order, so a failed reservation leaves the
    account's current username untouched. Historical ownership (procedure/
    evidence attribution, which is keyed on the STABLE users.id /
    external_subject, never on this display string) is unaffected."""
    validated = ug.validate_username(new_username)
    current = await get_profile(executor, user_id)
    old = (current or {}).get("username")
    if old and old.lower() == validated.lower():
        # Case-only change of the caller's OWN current name (e.g.
        # "copperfox" -> "CopperFox") -- not a rename that needs a history
        # entry, since nothing else could ever have claimed this exact
        # name in the meantime (it was already reserved to this account).
        try:
            return await _try_claim_username(executor, user_id, validated, only_if_unset=False) or current
        except asyncpg.exceptions.UniqueViolationError:
            return current

    try:
        updated = await _try_claim_username(executor, user_id, validated, only_if_unset=False)
    except asyncpg.exceptions.UniqueViolationError:
        raise ValueError("that username is already taken") from None
    if old:
        await executor.execute(
            "INSERT INTO username_history (user_id, old_username) VALUES ($1, $2)",
            user_id, old,
        )
    return updated


async def get_profile_by_username(executor: Any, username: str) -> Optional[dict]:
    """Resolve a username to its owning profile -- checks the ACTIVE
    username first, then username_history, so a link built from someone's
    old name still finds their current profile (never a different
    person's). Returns the row (with a `renamed_to` key set when the
    match came from history, None when it's current) or None when the
    name has never belonged to anyone."""
    row = await executor.fetchrow(
        f"SELECT {_PROFILE_COLS} FROM contributor_profiles WHERE lower(username) = lower($1)",  # noqa: S608
        username,
    )
    if row is not None:
        out = dict(row)
        out["renamed_to"] = None
        return out

    hist = await executor.fetchrow(
        "SELECT user_id FROM username_history WHERE lower(old_username) = lower($1)",
        username,
    )
    if hist is None:
        return None
    current = await get_profile(executor, str(hist["user_id"]))
    if current is None or not current.get("username"):
        return None
    out = dict(current)
    out["renamed_to"] = current["username"]
    return out


async def set_avatar(executor: Any, user_id: str, locator: Optional[str]) -> Optional[dict]:
    """Set (or, with locator=None, clear) the caller's avatar object
    locator. A REPLACE always writes a brand-new locator (the object-store
    layer is content-addressed, so a new image is automatically a new
    key/version) -- never edits an existing object in place -- so the
    delivery endpoint's cache headers can be immutable per-locator without
    ever serving a stale image after a replace."""
    row = await executor.fetchrow(
        f"""
        UPDATE contributor_profiles SET avatar_locator = $2, t_updated = now()
        WHERE user_id = $1
        RETURNING {_PROFILE_COLS}
        """,  # noqa: S608
        user_id, locator,
    )
    return dict(row) if row else None


async def complete_onboarding(executor: Any, user_id: str) -> Optional[dict]:
    row = await executor.fetchrow(
        f"""
        UPDATE contributor_profiles SET onboarding_complete = TRUE, t_updated = now()
        WHERE user_id = $1
        RETURNING {_PROFILE_COLS}
        """,  # noqa: S608
        user_id,
    )
    return dict(row) if row else None


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
        from app.services.shards import fanout_sum_row
        prow = await fanout_sum_row(       # a contributor's public procedures may live on any shard
            executor,
            """
            SELECT
              count(*)                                                 AS authored,
              count(*) FILTER (WHERE verification_state = 'verified')   AS verified
            FROM procedures
            WHERE owner_id = $1 AND t_invalid IS NULL
            """,
            subject,
        )
        counts["procedures_authored"] = int(prow.get("authored") or 0)
        counts["verified_procedures"] = int(prow.get("verified") or 0)

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
        SELECT u.id AS user_id, u.display_name, p.username, p.tagline, p.t_created AS profile_since
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
        "username": row["username"],
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
