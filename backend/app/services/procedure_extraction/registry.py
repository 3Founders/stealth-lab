"""
Extractor registry (procedure extraction, memory-substrate map).
procedure_extractors (migration 20) is modeled directly on
07_agents.sql's `agents` table -- this repo's existing pattern for a
config-driven, bi-temporal, reviewable artifact, including its
deliberate split between "approved" (cleared for listing/review) and
"enabled" (cleared to actually be selected).

TWO LAYERS OF EXTENSIBILITY, kept deliberately separate (see
strategies.py's own docstring for the mechanism side): a
ExtractionStrategy subclass is a MECHANISM, changed by writing code and
deploying it. A procedure_extractors row is a VARIANT of a mechanism
(prompt, model, params, scope) -- changed by inserting a new version
row. Pretending prompt tuning needs a code deploy, or that a genuinely
new analysis approach can be expressed as a config row, both produce the
wrong kind of system; this module only handles the second kind of
change.

VERSIONS SUPERSEDE, NEVER EDIT IN PLACE -- same discipline
supersede_procedure() uses for procedures themselves: improving an
extractor writes a new (name, version) row and sets the OLD row's
t_invalid, so "which extractor version produced this procedure" (via
procedures.extracted_by) stays answerable forever. This is invariants 1
and 2 (provenance, temporal meaning) applied to the extractor itself,
not just to what it produces.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.services.applicability import _scope_matches


async def create_extractor_version(
    pool: asyncpg.Pool,
    *,
    name: str,
    description: str,
    kind: str,
    version: str,
    config: Optional[dict] = None,
    scope: Optional[dict] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
) -> str:
    """
    Inserts a new version row, ALWAYS starting review_state='proposed',
    enabled=FALSE -- same "nothing is born verified/approved" discipline
    procedures.py's capture_procedure() already follows. If a prior
    version of the SAME name exists, this does not touch it here --
    superseding the old version's t_invalid is a separate, explicit
    action (supersede_extractor_version, below), not an automatic side
    effect of proposing a new one. A proposed-but-not-yet-approved
    candidate must not silently retire a working extractor.
    """
    if kind not in ("deterministic", "llm", "composite"):
        raise ValueError(f"kind must be one of deterministic/llm/composite, got {kind!r}")

    row_id = await pool.fetchval(
        "INSERT INTO procedure_extractors "
        "(name, description, kind, version, config, scope, owner_id, visibility) "
        "VALUES ($1,$2,$3::extractor_kind,$4,$5,$6,$7,$8::visibility_level) "
        "RETURNING id",
        name, description, kind, version, config or {}, scope or {}, owner_id, visibility,
    )
    return str(row_id)


async def supersede_extractor_version(
    pool: asyncpg.Pool, *, old_id: str, new_id: str,
) -> None:
    """
    Marks `old_id` t_invalid=now() -- the row still exists and is still
    queryable (procedures.extracted_by referencing it stays resolvable),
    it just stops being selectable. Does NOT flip enabled on `new_id`;
    that is a separate, explicit review action (approve_extractor
    below), same "approved != enabled" distinction 07_agents.sql's
    `runnable` column already makes for agents.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE procedure_extractors SET t_invalid = now() WHERE id = $1::uuid", old_id,
            )


async def approve_extractor(
    pool: asyncpg.Pool, *, extractor_id: str, approver: str, enable: bool = True,
) -> None:
    """Explicit human action -- review_state='approved', and enabled set
    per the caller's own choice (a reviewer may approve for
    listing/comparison without yet trusting it for automatic
    selection)."""
    await pool.execute(
        "UPDATE procedure_extractors SET review_state = 'approved'::extractor_review_state, "
        "enabled = $2 WHERE id = $1::uuid",
        extractor_id, enable,
    )


async def reject_extractor(pool: asyncpg.Pool, *, extractor_id: str, approver: str) -> None:
    await pool.execute(
        "UPDATE procedure_extractors SET review_state = 'rejected'::extractor_review_state, "
        "enabled = FALSE WHERE id = $1::uuid",
        extractor_id,
    )


async def select_extractor(
    pool: asyncpg.Pool, *, current_scope: Optional[dict] = None,
) -> Optional[dict]:
    """
    Real selection: among enabled AND approved extractors whose scope
    matches `current_scope` (reusing applicability._scope_matches --
    same scope-narrowing semantics, not a second implementation of it),
    pick the highest version, preferring a non-deterministic kind when
    tied (an llm/composite extractor is a strictly richer attempt than
    the baseline when both are otherwise equally eligible).

    Returns None if nothing is selectable -- the caller's real signal to
    fall back to the seeded 'deterministic_v1' baseline (migration 20
    seeds it enabled=TRUE precisely so this fallback always has
    somewhere to land), not an error.
    """
    rows = await pool.fetch(
        "SELECT id, name, description, kind, version, config, scope "
        "FROM procedure_extractors "
        "WHERE enabled AND review_state = 'approved' AND t_invalid IS NULL",
    )
    current_scope = current_scope or {}
    candidates = [r for r in rows if _scope_matches(dict(r["scope"] or {}), current_scope)]
    if not candidates:
        return None

    def _sort_key(r):
        # Version is TEXT, not an integer column -- best-effort numeric
        # comparison, falling back to string comparison for a
        # non-numeric version scheme rather than raising.
        try:
            v = float(r["version"])
        except (TypeError, ValueError):
            v = 0.0
        return (v, r["kind"] != "deterministic")

    candidates.sort(key=_sort_key, reverse=True)
    best = candidates[0]
    return dict(best)


async def extractor_stats(pool: asyncpg.Pool, *, extractor_id: str, name: str) -> dict:
    """
    Three real signals, in increasing order of value and DECREASING
    order of availability -- report all three rather than leaning on
    the cheap ones:
      1. validator pass rate -- cheap, available immediately, easy to
         game (an extractor that emits trivial procedures passes V1-V5
         effortlessly without being USEFUL).
      2. human approval rate -- procedures.approval_status among
         procedures this extractor produced.
      3. downstream success rate -- record_execution_outcome results
         rolled up by procedures.extracted_by. The one that actually
         matters, and necessarily delayed: it only exists once a
         procedure this extractor produced has been RE-USED and its
         outcome recorded.
    """
    tag = f"{name}@{extractor_id}"
    counts = await pool.fetchrow(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE approval_status = 'approved') AS approved, "
        "count(*) FILTER (WHERE approval_status = 'rejected') AS rejected "
        "FROM procedures WHERE extracted_by = $1",
        tag,
    )
    total = counts["total"] or 0
    approved = counts["approved"] or 0
    outcomes = await pool.fetchrow(
        "SELECT COALESCE(SUM((verification_stats->>'successes')::int), 0) AS successes, "
        "COALESCE(SUM((verification_stats->>'attempts')::int), 0) AS attempts "
        "FROM procedures WHERE extracted_by = $1",
        tag,
    )
    return {
        "extracted_by": tag,
        "procedures_produced": total,
        "human_approval_rate": (approved / total) if total else None,
        "downstream_attempts": outcomes["attempts"] or 0,
        "downstream_successes": outcomes["successes"] or 0,
        "downstream_success_rate": (
            (outcomes["successes"] / outcomes["attempts"]) if outcomes["attempts"] else None
        ),
    }
